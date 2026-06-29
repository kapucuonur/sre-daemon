# Graph Report - sre  (2026-06-28)

## Corpus Check
- 19 files · ~12,493 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 256 nodes · 393 edges · 17 communities (10 shown, 7 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 22 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a7bf7d74`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]

## God Nodes (most connected - your core abstractions)
1. `DockerWatcher` - 18 edges
2. `JournalWatcher` - 18 edges
3. `HealingOrchestrator` - 13 edges
4. `main()` - 13 edges
5. `analyze()` - 12 edges
6. `update_strategy_result()` - 11 edges
7. `get_best_strategy()` - 10 edges
8. `compute_error_hash()` - 9 edges
9. `register_actions_in_registry()` - 9 edges
10. `telegram_poller()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `HealingOrchestrator` --uses--> `DockerWatcher`  [INFERRED]
  sre_daemon.py → monitors/docker.py
- `RateLimiter` --uses--> `DockerWatcher`  [INFERRED]
  sre_daemon.py → monitors/docker.py
- `HealingOrchestrator` --uses--> `JournalWatcher`  [INFERRED]
  sre_daemon.py → monitors/journal.py
- `RateLimiter` --uses--> `JournalWatcher`  [INFERRED]
  sre_daemon.py → monitors/journal.py
- `SRE Daemon` --references--> `requests`  [INFERRED]
  README.md → requirements.txt

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Self-Healing Loop** — readme_sre_daemon, readme_llm_pipeline, readme_telegram_hitl, readme_watchdog [EXTRACTED 0.90]
- **Graphify Tooling Suite** — graphify_query, graphify_path, graphify_explain, graphify_update [EXTRACTED 1.00]

## Communities (17 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.10
Nodes (28): load_env(), Any, Path, add_pending_action(), append_incident_to_graph_doc(), atomic_write_text(), get_daemon_setting(), get_heal_history_for_hash() (+20 more)

### Community 1 - "Community 1"
Cohesion: 0.13
Nodes (9): cleanup_old_prefix_tags(), DockerWatcher, JournalWatcher, main(), RateLimiter, Monitors daemon.log for exceptions and triggers self-fix loops., start_heartbeat(), start_self_monitor() (+1 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (13): BaseWatcher, Stop the watcher loop., Base class for all system/application monitor watchers., Start the watcher loop in a separate thread., DockerWatcher, JournalWatcher, Pattern, AnthropicClient (+5 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (35): analyze(), analyze_with_claude_api(), analyze_with_gemini(), analyze_with_groq(), analyze_with_litellm(), analyze_with_ollama(), analyze_with_xai(), build_prompt() (+27 more)

### Community 5 - "Community 5"
Cohesion: 0.25
Nodes (8): graphify explain, graphify-out/, graphify path, graphify query, Graphify Rules, Graphify Skill, graphify update, Graphify Workflow

### Community 6 - "Community 6"
Cohesion: 0.25
Nodes (8): Docker Events, 6-Tier LLM Fallback Pipeline, SQLite HITL State Machine, SRE Daemon, systemd journal, Telegram Approval Gateway, Independent Watchdog, requests

### Community 7 - "Community 7"
Cohesion: 0.13
Nodes (14): 1. Clone the repository and install dependencies:, 2. Configure environment variables (`.env`):, 3. Start as a systemd service:, Architecture, 📱 ChatOps & Visual Monitoring, Features, Future Roadmap & Vision (v6.0 Planning), How It Works (+6 more)

### Community 15 - "Community 15"
Cohesion: 0.06
Nodes (32): Incident: Autonomous Healing: [AI-Coach], Incident: Autonomous Healing: [Another_mockservice], Incident: Autonomous Healing: [Another_mockservice], Incident: Autonomous Healing: [BikeFit-API], Incident: Autonomous Healing: [BikeFit-API], Incident: Autonomous Healing: [BikeFit-API], Incident: Autonomous Healing: [Mockservice], Incident: Autonomous Healing: [Mockservice] (+24 more)

### Community 16 - "Community 16"
Cohesion: 0.10
Nodes (13): compute_error_hash(), get_best_strategy(), register_actions_in_registry(), update_strategy_result(), db_path(), tests/test_strategy_registry.py Pytest unit tests — strategy registry fonksiyonl, 1. İlk hata: registry boş → None döner (LLM devreye girer)         2. LLM başarı, Her test için geçici SQLite DB. (+5 more)

## Knowledge Gaps
- **62 isolated node(s):** `deploy_to_pi.sh script`, `journal_to_file.sh script`, `graphify`, `Workflow: graphify`, `Key Concepts` (+57 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `read_recent_log_lines()` connect `Community 3` to `Community 0`?**
  _High betweenness centrality (0.084) - this node is a cross-community bridge._
- **Why does `load_env()` connect `Community 0` to `Community 3`?**
  _High betweenness centrality (0.063) - this node is a cross-community bridge._
- **Why does `update_strategy_result()` connect `Community 16` to `Community 0`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `DockerWatcher` (e.g. with `BaseWatcher` and `AnthropicClient`) actually correct?**
  _`DockerWatcher` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `JournalWatcher` (e.g. with `BaseWatcher` and `AnthropicClient`) actually correct?**
  _`JournalWatcher` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `HealingOrchestrator` (e.g. with `DockerWatcher` and `JournalWatcher`) actually correct?**
  _`HealingOrchestrator` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Pre-processes and summarizes logs to reduce tokens sent to LLM.`, `Son çalıştırma durumunu yükle`, `Docker container loglarını al` to the rest of the system?**
  _90 weakly-connected nodes found - possible documentation gaps or missing edges._