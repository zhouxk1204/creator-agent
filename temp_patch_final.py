to_patch = {
    r"tests/unit/test_naming.py": [
        ('assert base == "2024-06-15_Test_Creator"',
         'assert base == "2024-06-15"'),
        ('assert video_folder_name("Test Creator", dt, 1) == "2024-06-15_Test_Creator_2"',
         'assert video_folder_name(dt, 1) == "2024-06-15_2"'),
    ],
    r"tests/integration/test_repository.py": [
        ('assert v2.storage_path == "2024-06-15_Test_Creator"',
         'assert v2.storage_path == "2024-06-15"'),
        ('assert v2.storage_path == "2024-06-15_Test_Creator_2"',
         'assert v2.storage_path == "2024-06-15_2"'),
    ],
}

for fpath, replacements in to_patch.items():
    content = open(fpath, encoding="utf-8").read()
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            print(f"  {fpath}: patched")
        else:
            print(f"  {fpath}: WARNING - not found:\n    {old}")
    open(fpath, "w", encoding="utf-8").write(content)

print("Done!")