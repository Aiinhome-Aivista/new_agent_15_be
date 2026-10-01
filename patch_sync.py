import re

with open(r'd:\New Agent\new_agent_15\backend\app\services\sync_service.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace block 1 (Extraction)
pattern_extract = re.compile(r'# ── Extract repository details from description if present ──.*?ref_repo_info =\s*\{\s*"name": ref_parsed,\s*"url": ref_url,\s*"branch": ref_branch,\s*"is_reference": True\s*\}\n', re.DOTALL)

replacement_extract = r'''# ── Extract repository details from description if present ──
                from app.services.token_secret_service import TokenSecretService

                story_repos = []

                # Find all Target Repos
                # We use finditer to match multiple "Target Repo: xxx"
                target_repo_matches = list(re.finditer(r'(?:^|\n)\s*(?:Target\s*Repo(?:sitory)?|Target|Repository|Repo)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE))
                target_repo_indices = [m.start() for m in target_repo_matches]

                if not target_repo_matches:
                    story_repos.append({
                        "name": "main-repo",
                        "url": default_repo_url,
                        "branch": default_base_branch,
                        "external_assignee": task.assignee_email,
                        "priority": task_prio
                    })
                else:
                    for i, m in enumerate(target_repo_matches):
                        parsed_name = m.group(1).strip().strip('`').strip('"').strip("'")
                        
                        start_idx = m.end()
                        end_idx = target_repo_indices[i+1] if i + 1 < len(target_repo_indices) else len(raw_desc)
                        block = raw_desc[start_idx:end_idx]
                        
                        ref_idx = re.search(r'(?:^|\n)\s*(?:Reference\s*Repo|Ref\s*Repo|Reference)\s*[:\-]', block, re.IGNORECASE)
                        if ref_idx:
                            block = block[:ref_idx.start()]
                        
                        b_match = re.search(r'(?:^|\n)\s*(?:Target\s*Branch|Base\s*Branch|Source\s*Branch|Branch)\s*[:\-]\s*([^\s\n\r]+)', block, re.IGNORECASE)
                        u_match = re.search(r'(?:^|\n)\s*(?:Target\s*URL|Target\s*Git|URL|Link|Git)\s*[:\-]\s*(https?://[^\s\n\r]+|git@[^\s\n\r]+)', block, re.IGNORECASE)
                        
                        t_name = parsed_name
                        t_url = default_repo_url
                        t_branch = default_base_branch
                        
                        repo_cfg = TokenSecretService.get_repo_config(parsed_name)
                        if repo_cfg:
                            if repo_cfg.get('url'):
                                t_url = repo_cfg.get('url')
                            if repo_cfg.get('branch'):
                                t_branch = repo_cfg.get('branch')
                        
                        if u_match:
                            t_url = u_match.group(1).strip().strip('`')
                        if b_match:
                            t_branch = b_match.group(1).strip().strip('`')
                            
                        repo_info = {
                            "name": t_name,
                            "url": t_url,
                            "branch": t_branch,
                            "external_assignee": task.assignee_email,
                            "priority": task_prio
                        }
                        if task.due_date:
                            repo_info["due_date"] = task.due_date
                        story_repos.append(repo_info)

                target_repo_branch = story_repos[0]["branch"] if story_repos else default_base_branch

                # Optional Reference Repo
                ref_repo_m = re.search(r'(?:^|\n)\s*(?:Reference\s*Repo(?:sitory)?|Ref\s*Repo(?:sitory)?|Reference)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)
                ref_branch_m = re.search(r'(?:^|\n)\s*(?:Reference\s*Branch|Ref\s*Branch)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)
                ref_repo_info = None
                if ref_repo_m:
                    ref_parsed = ref_repo_m.group(1).strip().strip('`').strip('"').strip("'")
                    ref_cfg = TokenSecretService.get_repo_config(ref_parsed)
                    ref_url = (ref_cfg.get('url') if ref_cfg else None) or (ref_parsed if ref_parsed.startswith('http') or ref_parsed.startswith('git@') else '')
                    ref_branch = (ref_branch_m.group(1).strip().strip('`') if ref_branch_m else None) or (ref_cfg.get('branch') if ref_cfg else 'main') or 'main'
                    if ref_parsed and ref_url:
                        ref_repo_info = {
                            "name": ref_parsed,
                            "url": ref_url,
                            "branch": ref_branch,
                            "is_reference": True
                        }
                if ref_repo_info:
                    story_repos.append(ref_repo_info)
'''

match = pattern_extract.search(content)
if match:
    content = content[:match.start()] + replacement_extract + content[match.end():]
else:
    print("Match 1 not found!")

# Now replace block 2
pattern_update = re.compile(r'details = \[dict\(d\) for d in \(existing\.repository_details or \[\{\}\]\)\].*?updated = True\n', re.DOTALL)
replacement_update = r'''# Keep story_points if it exists in previous details and isn't provided by task
                    old_details = [dict(d) for d in (existing.repository_details or [{}])]
                    if old_details and isinstance(old_details[0], dict):
                        old_sp = old_details[0].get('story_points')
                        if task.story_points is not None:
                            old_sp = task.story_points
                        if old_sp is not None:
                            for sr in story_repos:
                                sr['story_points'] = old_sp

                    # Compare and update repository details if changed
                    import json
                    if json.dumps(old_details, sort_keys=True) != json.dumps(story_repos, sort_keys=True):
                        existing.repository_details = story_repos
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(existing, 'repository_details')
                        updated = True
'''
match2 = pattern_update.search(content)
if match2:
    content = content[:match2.start()] + replacement_update + content[match2.end():]
else:
    print("Match 2 not found!")

# Now replace block 3
pattern_end = re.compile(r'repo_info = \{(?:\n.*){8}if ref_repo_info:\n.*story_repos\.append\(ref_repo_info\)', re.DOTALL)
replacement_end = r'''# story_repos list is already built during the extraction phase above.
                # Just passing through to Story creation.'''

match3 = pattern_end.search(content)
if match3:
    content = content[:match3.start()] + replacement_end + content[match3.end():]
else:
    print("Match 3 not found!")


with open(r'd:\New Agent\new_agent_15\backend\app\services\sync_service.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done replacement')
