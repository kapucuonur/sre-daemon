import os
import sys
import re
sys.path.append("/home/pi/sre")
from sre_daemon import HealingOrchestrator, FileEditor

def test_diagnostics():
    orchestrator = HealingOrchestrator()
    
    # 1. Mockup traceback
    mock_traceback = """
2026-06-29T14:00:00 [ERROR] bikefit-api crash traceback:
Traceback (most recent call last):
  File "/home/pi/sre/tests/test_file_editor.py", line 12, in test_file_editor
    initial_content = "def add(a, b):"
AssertionError: test error
"""
    
    # 2. Extract context via re.search like inside _heal
    match = re.search(r'File "(/home/pi/[^"]+\.py)", line (\d+)', mock_traceback)
    assert match is not None, "Traceback search regex fails to match mock log!"
    
    target_file = match.group(1)
    line_no = int(match.group(2))
    
    assert target_file == "/home/pi/sre/tests/test_file_editor.py", f"Target file mismatch: {target_file}"
    assert line_no == 12, f"Line number mismatch: {line_no}"
    print("1. Traceback parsing ve satır numarası ayıklama başarılı.")
    
    # 3. Context okuma testi
    start_line = max(1, line_no - 20)
    end_line = line_no + 20
    raw_code = orchestrator.file_editor.read_file(target_file, start_line, end_line)
    
    assert raw_code and not raw_code.startswith("Error"), f"Read file failed: {raw_code}"
    assert "test_file_editor" in raw_code, "Read file content mismatch!"
    print("2. Kod bağlamı (file context) başarıyla okundu.")
    
    # 4. _build_prompt test
    prompt = orchestrator._build_prompt("mock_error", "[Mock-Service]", "mock_hash", context_section=raw_code)
    assert "test_file_editor" in prompt, "Prompt does not contain the code context section!"
    assert "replace" in prompt, "Prompt does not mention replace action!"
    print("3. LLM Prompt build ve context enjeksiyonu başarılı.")
    
    # 5. Telegram Diff Mock test
    actions = [
        {
            "type": "replace",
            "target": "/home/pi/sre/tests/test_file_editor.py",
            "search": "initial_content = \"def add(a, b):\"",
            "replace": "initial_content = \"def add(a, b):\" # fixed comment"
        }
    ]
    
    # Simulated approval loops
    actions_summary = ""
    for act in actions:
        act_type = act.get("type", "")
        target = act.get("target", "")
        if act_type == "replace":
            search = act.get("search", "")
            replace = act.get("replace", "")
            import difflib
            diff = list(difflib.unified_diff(
                search.splitlines(),
                replace.splitlines(),
                fromfile="old_code",
                tofile="new_code",
                lineterm=""
            ))
            diff_text = "\n".join(diff[2:])
            actions_summary += f"• replace -> {target}:\n{diff_text}\n"
            
    assert "+initial_content = \"def add(a, b):\" # fixed comment" in actions_summary, "Unified diff output generation failed!"
    print("4. Telegram görsel diff üretimi başarılı.")
    print("✅ Tüm diagnostic testleri geçti!")

if __name__ == "__main__":
    try:
        test_diagnostics()
    except AssertionError as e:
        print(f"❌ TEST HATA: {e}")
        sys.exit(1)
