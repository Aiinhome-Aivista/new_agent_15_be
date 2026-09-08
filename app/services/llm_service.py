import os
import requests
import google.generativeai as genai
from flask import current_app

class LLMService:
    @staticmethod
    def generate_response(prompt, system_instruction=None, provider_override=None):
        """
        Routes the prompt to the appropriate LLM provider.
        """
        # Allow dynamic override, otherwise use the environment default
        provider = provider_override or current_app.config.get("LLM_PROVIDER", "gemini").lower()

        if provider == "gemini":
            return LLMService._call_gemini(prompt, system_instruction)
        elif provider in ["custom", "local"]:
            return LLMService._call_local(prompt, system_instruction)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    @staticmethod
    def _call_gemini(prompt, system_instruction=None):
        api_key = current_app.config.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")

        genai.configure(api_key=api_key)
        model_name = current_app.config.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
        
        # Configure model with system instruction if provided
        kwargs = {}
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
            
        model = genai.GenerativeModel(model_name, **kwargs)
        
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            raise RuntimeError(f"Gemini API Error: {str(e)}")

    @staticmethod
    def _call_local(prompt, system_instruction=None):
        api_url = current_app.config.get("LLM_API_URL")
        model = current_app.config.get("LLM_MODEL")
        timeout = current_app.config.get("LLM_TIMEOUT", 300)

        if not api_url:
            raise ValueError("LLM_API_URL is not configured for local provider.")

        # Constructing payload assuming standard completion API (like Ollama or vLLM)
        # Adapt payload structure based on the specific local LLM in use.
        payload = {
            "model": model,
            "prompt": f"{system_instruction}\n\n{prompt}" if system_instruction else prompt,
            "stream": False
        }

        try:
            response = requests.post(api_url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            # Depending on API, response key might be 'response' (Ollama) or 'text'
            return data.get("response", data.get("text", str(data)))
        except requests.RequestException as e:
            raise RuntimeError(f"Local LLM API Error: {str(e)}")
