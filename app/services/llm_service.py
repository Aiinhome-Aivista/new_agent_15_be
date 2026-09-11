"""
LLMService — Routes prompts to the configured LLM provider.

Multi-key Gemini support:
  - Each agent has a dedicated Gemini key (KEY_1 to KEY_5) to spread quota.
  - If a dedicated key is not set, falls back to round-robin across all
    configured keys, then to the primary GEMINI_API_KEY.
  - Usage: LLMService.generate_response(prompt, agent_name="Developer")
"""
import itertools
import logging
import requests
import google.generativeai as genai
from flask import current_app

logger = logging.getLogger(__name__)

# Module-level round-robin iterator (shared across requests)
_rr_cycle = None
_rr_keys: list[str] = []


def _build_key_pool(config: dict) -> list[str]:
    """Collect all non-empty Gemini keys into a deduplicated ordered list."""
    candidates = [
        config.get("GEMINI_API_KEY_1"),
        config.get("GEMINI_API_KEY_2"),
        config.get("GEMINI_API_KEY_3"),
        config.get("GEMINI_API_KEY_4"),
        config.get("GEMINI_API_KEY_5"),
        config.get("GEMINI_API_KEY"),   # primary key last (fallback)
    ]
    seen = set()
    pool = []
    for k in candidates:
        if k and k.strip() and k.strip() not in seen:
            seen.add(k.strip())
            pool.append(k.strip())
    return pool


def _get_gemini_key(config: dict, agent_name: str | None = None) -> str:
    """
    Returns the best Gemini API key for the given agent.

    Priority:
      1. Dedicated key for this agent (from GEMINI_AGENT_KEY_MAP)
      2. Round-robin across all configured keys
      3. Primary GEMINI_API_KEY
    Raises ValueError if no key is available.
    """
    global _rr_cycle, _rr_keys

    # ── 1. Per-agent dedicated key ────────────────────────────
    if agent_name:
        agent_key_map: dict = config.get("GEMINI_AGENT_KEY_MAP", {})
        env_var_name = agent_key_map.get(agent_name)
        if env_var_name:
            dedicated = config.get(env_var_name, "").strip()
            if dedicated:
                logger.debug(f"[LLMService] Agent '{agent_name}' using dedicated key {env_var_name}")
                return dedicated

    # ── 2. Round-robin pool ───────────────────────────────────
    pool = _build_key_pool(config)
    if pool:
        # Rebuild cycle only if pool changed
        if pool != _rr_keys:
            _rr_keys = pool
            _rr_cycle = itertools.cycle(pool)
            logger.info(f"[LLMService] Key pool built: {len(pool)} key(s) in rotation")
        key = next(_rr_cycle)
        logger.debug(f"[LLMService] Round-robin key selected (pool size={len(pool)})")
        return key

    raise ValueError(
        "No Gemini API key configured. Set GEMINI_API_KEY or GEMINI_API_KEY_1..5 in .env"
    )


class LLMService:

    @staticmethod
    def generate_response(
        prompt: str,
        system_instruction: str | None = None,
        provider_override: str | None = None,
        agent_name: str | None = None,
    ) -> str:
        """
        Route the prompt to the appropriate LLM provider.

        Args:
            prompt:             The user/task prompt.
            system_instruction: Optional system-level instruction.
            provider_override:  Force a specific provider (overrides config).
            agent_name:         Name of the calling agent (e.g. 'Developer').
                                Used to select the dedicated Gemini key.
        """
        provider = (
            provider_override
            or current_app.config.get("LLM_PROVIDER", "gemini")
        ).lower()

        if provider == "gemini":
            try:
                return LLMService._call_gemini(prompt, system_instruction, agent_name)
            except Exception as e:
                # If Gemini fails, check if local/custom LLM endpoint is available as fallback
                local_url = current_app.config.get("LLM_API_URL")
                if local_url:
                    logger.warning(f"[LLMService] Gemini failed for agent '{agent_name}' ({e}). Falling back to local LLM ({local_url})...")
                    try:
                        return LLMService._call_local(prompt, system_instruction)
                    except Exception as local_e:
                        logger.error(f"[LLMService] Local LLM fallback also failed: {local_e}")
                raise e
        elif provider in ("custom", "local"):
            return LLMService._call_local(prompt, system_instruction)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    # ──────────────────────────────────────────────────────────
    # Provider implementations
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _call_gemini(
        prompt: str,
        system_instruction: str | None = None,
        agent_name: str | None = None,
    ) -> str:
        config = current_app.config
        api_key = _get_gemini_key(config, agent_name)

        genai.configure(api_key=api_key)
        preferred_model = config.get("GEMINI_MODEL", "gemini-1.5-flash")

        kwargs = {}
        if system_instruction:
            kwargs["system_instruction"] = system_instruction

        candidate_models = [
            preferred_model,
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-2.0-flash",
            "gemini-pro"
        ]
        # Deduplicate candidates while keeping order
        seen_models = set()
        models_to_try = []
        for m in candidate_models:
            if m and m not in seen_models:
                seen_models.add(m)
                models_to_try.append(m)

        last_error = None
        for m_name in models_to_try:
            try:
                model = genai.GenerativeModel(m_name, **kwargs)
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                last_error = e
                err_msg = str(e)
                if "404" in err_msg or "not found" in err_msg.lower():
                    logger.warning(f"[LLMService] Model '{m_name}' returned 404 for agent '{agent_name}'. Trying next model candidate...")
                    continue
                else:
                    break

        logger.error(f"[LLMService] Gemini API error (agent={agent_name}): {last_error}")
        raise RuntimeError(f"Gemini API Error: {str(last_error)}")

    @staticmethod
    def _call_local(
        prompt: str,
        system_instruction: str | None = None,
    ) -> str:
        config = current_app.config
        api_url = config.get("LLM_API_URL")
        model   = config.get("LLM_MODEL")
        timeout = config.get("LLM_TIMEOUT", 300)

        if not api_url:
            raise ValueError("LLM_API_URL is not configured for local provider.")

        payload = {
            "model":  model,
            "prompt": f"{system_instruction}\n\n{prompt}" if system_instruction else prompt,
            "stream": False,
        }

        try:
            response = requests.post(api_url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            return data.get("response", data.get("text", str(data)))
        except requests.RequestException as e:
            raise RuntimeError(f"Local LLM API Error: {str(e)}")
