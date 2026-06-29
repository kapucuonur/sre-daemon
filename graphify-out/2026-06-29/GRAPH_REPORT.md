# Graph Report - sre  (2026-06-29)

## Corpus Check
- 24 files · ~31,292 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 546 nodes · 775 edges · 23 communities (15 shown, 8 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 20 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c74fd02d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]

## God Nodes (most connected - your core abstractions)
1. `DockerWatcher` - 20 edges
2. `JournalWatcher` - 20 edges
3. `🤖 SRE Daemon` - 16 edges
4. `HealingOrchestrator` - 15 edges
5. `main()` - 13 edges
6. `main()` - 13 edges
7. `analyze()` - 12 edges
8. `update_strategy_result()` - 11 edges
9. `FileEditor` - 11 edges
10. `HealingOrchestrator` - 11 edges

## Surprising Connections (you probably didn't know these)
- `HealingOrchestrator` --uses--> `DockerWatcher`  [INFERRED]
  sre_daemon.py → monitors/docker.py
- `HealingOrchestrator` --uses--> `JournalWatcher`  [INFERRED]
  sre_daemon.py → monitors/journal.py
- `🤖 SRE Daemon` --references--> `requests`  [INFERRED]
  README.md → requirements.txt
- `AnthropicClient` --uses--> `DockerWatcher`  [INFERRED]
  sre_daemon.py → monitors/docker.py
- `GeminiClient` --uses--> `DockerWatcher`  [INFERRED]
  sre_daemon.py → monitors/docker.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Self-Healing Loop** — readme_sre_daemon, readme_llm_pipeline, readme_telegram_hitl, readme_watchdog [EXTRACTED 0.90]
- **Graphify Tooling Suite** — graphify_query, graphify_path, graphify_explain, graphify_update [EXTRACTED 1.00]

## Communities (23 total, 8 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (45): add_pending_action(), append_incident_to_graph_doc(), atomic_write_text(), cleanup_old_prefix_tags(), DockerWatcher, get_daemon_setting(), get_heal_history_for_hash(), get_telegram_status_report() (+37 more)

### Community 1 - "Community 1"
Cohesion: 0.18
Nodes (13): add_pending_action(), get_daemon_setting(), get_telegram_status_report(), init_db(), Sistem sağlığı ve Docker durumlarını toplayıp Markdown rapor hazırlar., Autonomously registers a newly discovered service to config.json., Idempotent and thread-safe transition of pending actions., register_discovered_service() (+5 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (14): BaseWatcher, Stop the watcher loop., Base class for all system/application monitor watchers., Start the watcher loop in a separate thread., DockerWatcher, JournalWatcher, Pattern, AnthropicClient (+6 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (35): analyze(), analyze_with_claude_api(), analyze_with_gemini(), analyze_with_groq(), analyze_with_litellm(), analyze_with_ollama(), analyze_with_xai(), build_prompt() (+27 more)

### Community 5 - "Community 5"
Cohesion: 0.25
Nodes (8): graphify explain, graphify-out/, graphify path, graphify query, Graphify Rules, Graphify Skill, graphify update, Graphify Workflow

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (21): 1. Clone the repository and install dependencies:, 2. Configure environment variables (`.env`):, 3. Start as a systemd service:, Architecture & LLM Cascade Pipeline, Docker Events, Features, How It Works, Key Concepts (+13 more)

### Community 9 - "Community 9"
Cohesion: 0.15
Nodes (13): get_heal_history_for_hash(), HealingOrchestrator, llm_approve_for_whitelist(), _load_learned_patterns(), _notify_telegram_rejection(), _persist_learned_pattern(), Any, Dosyadan dinamik olarak öğrenilmiş whitelist pattern'lerini yükle. (+5 more)

### Community 15 - "Community 15"
Cohesion: 0.01
Nodes (207): Incident: Auto-remount Healing: [BikeFit-API], Incident: Autonomous Healing: [AI-Coach], Incident: Autonomous Healing: [AI-Coach], Incident: Autonomous Healing: [AI-Coach], Incident: Autonomous Healing: [AI-Coach], Incident: Autonomous Healing: [AI-Coach], Incident: Autonomous Healing: [Another_mockservice], Incident: Autonomous Healing: [Another_mockservice] (+199 more)

### Community 16 - "Community 16"
Cohesion: 0.10
Nodes (13): compute_error_hash(), get_best_strategy(), register_actions_in_registry(), update_strategy_result(), db_path(), tests/test_strategy_registry.py Pytest unit tests — strategy registry fonksiyonl, 1. İlk hata: registry boş → None döner (LLM devreye girer)         2. LLM başarı, Her test için geçici SQLite DB. (+5 more)

### Community 17 - "Community 17"
Cohesion: 0.21
Nodes (6): FileEditor, SRE Daemon File Management, Syntax Validation & Safe Patching Sandbox, Atomically writes content to the target file., Validates python file syntax using py_compile.         Returns: (is_valid: bool,, Attempts to apply a search-and-replace block, validates compile syntax,, test_file_editor()

### Community 18 - "Community 18"
Cohesion: 0.17
Nodes (14): append_incident_to_graph_doc(), atomic_write_text(), compute_error_hash(), get_best_strategy(), load_env(), md_escape(), Path, Pre-processes and summarizes logs to reduce tokens sent to LLM. (+6 more)

### Community 19 - "Community 19"
Cohesion: 0.13
Nodes (9): cleanup_old_prefix_tags(), DockerWatcher, JournalWatcher, main(), RateLimiter, Monitors daemon.log for exceptions and triggers self-fix loops., start_heartbeat(), start_self_monitor() (+1 more)

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (6): AnthropicClient, GeminiClient, GroqClient, MacChecker, OllamaClient, XAIClient

### Community 21 - "Community 21"
Cohesion: 0.24
Nodes (5): FileEditor, SRE Daemon File Management, Syntax Validation & Safe Patching Sandbox, Atomically writes content to the target file., Validates python file syntax using py_compile.         Returns: (is_valid: bool,, Attempts to apply a search-and-replace block, validates compile syntax,

## Knowledge Gaps
- **238 isolated node(s):** `deploy_to_pi.sh script`, `install.sh script`, `journal_to_file.sh script`, `graphify`, `Workflow: graphify` (+233 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `JournalWatcher` connect `Community 2` to `Community 0`, `Community 1`, `Community 19`?**
  _High betweenness centrality (0.055) - this node is a cross-community bridge._
- **Why does `DockerWatcher` connect `Community 2` to `Community 0`, `Community 1`, `Community 19`?**
  _High betweenness centrality (0.054) - this node is a cross-community bridge._
- **Why does `FileEditor` connect `Community 17` to `Community 0`, `Community 2`?**
  _High betweenness centrality (0.015) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `DockerWatcher` (e.g. with `BaseWatcher` and `AnthropicClient`) actually correct?**
  _`DockerWatcher` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `JournalWatcher` (e.g. with `BaseWatcher` and `AnthropicClient`) actually correct?**
  _`JournalWatcher` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `HealingOrchestrator` (e.g. with `DockerWatcher` and `JournalWatcher`) actually correct?**
  _`HealingOrchestrator` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Pre-processes and summarizes logs to reduce tokens sent to LLM.`, `Son çalıştırma durumunu yükle`, `Docker container loglarını al` to the rest of the system?**
  _289 weakly-connected nodes found - possible documentation gaps or missing edges._