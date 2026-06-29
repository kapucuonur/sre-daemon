import os
import sys
sys.path.append("/home/pi/sre")
from sre_daemon import FileEditor

def test_file_editor():
    editor = FileEditor()
    test_file = "/tmp/test_editor_target.py"
    
    # 1. Test dosyası oluştur
    initial_content = """# Hello world
def add(a, b):
    return a + b
"""
    with open(test_file, "w") as f:
        f.write(initial_content)
        
    print("1. Test dosyası oluşturuldu.")
    
    # 2. Read test
    content = editor.read_file(test_file, 1, 3)
    assert "add(a, b)" in content, "read_file başarısız!"
    print("2. read_file başarıyla test edildi.")
    
    # 3. Dry-run test (Syntax geçerli)
    success, detail = editor.apply_patch(
        test_file,
        search_block="    return a + b",
        replace_block="    # Adding comment\n    return a + b",
        dry_run=True
    )
    assert success is True, f"Dry run başarısız: {detail}"
    print("3. dry-run (Syntax geçerli) başarıyla test edildi.")
    
    # 4. Dry-run test (Syntax geçersiz - name error / compile error)
    success, detail = editor.apply_patch(
        test_file,
        search_block="    return a + b",
        replace_block="    return a + b +\n", # syntax error (Trailing plus)
        dry_run=True
    )
    assert success is False, f"Geçersiz syntax dry-run'da engellenmedi!"
    print("4. dry-run (Syntax geçersiz) başarıyla engellendi.")
    
    # 5. Gerçek yama test (dry_run=False)
    success, detail = editor.apply_patch(
        test_file,
        search_block="    return a + b",
        replace_block="    return a * b",
        dry_run=False
    )
    assert success is True, f"Gerçek yama başarısız: {detail}"
    assert os.path.exists(test_file + ".bak"), "Yedek (.bak) dosyası oluşturulmadı!"
    
    # Güncel içeriği doğrula
    with open(test_file, "r") as f:
        patched = f.read()
    assert "return a * b" in patched, "Yama içeriğe yansımadı!"
    print("5. Gerçek yama ve yedekleme (backup) başarıyla test edildi.")
    
    # Temizlik
    if os.path.exists(test_file): os.unlink(test_file)
    if os.path.exists(test_file + ".bak"): os.unlink(test_file + ".bak")
    print("✅ Tüm testler başarıyla geçti!")

if __name__ == "__main__":
    try:
        test_file_editor()
    except AssertionError as e:
        print(f"❌ TEST HATA: {e}")
        sys.exit(1)
