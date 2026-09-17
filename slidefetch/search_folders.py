from pathlib import Path

print("Folder Search Tool")
print("Type 'exit' to quit\n")

base_dir = Path(__file__).resolve().parent
search_targets = [
    ("downloads", base_dir / "downloads", False),
    ("downloads/new", base_dir / "downloads" / "new", False),
    ("dataset", base_dir / "dataset", True),
    ("scraper/dataset", base_dir.parent / "scraper" / "dataset", True),
]  # (label, path, recursive)

while True:
    search_term = input("Enter search term: ").strip()
    
    if search_term.lower() == "exit":
        print("Exiting...")
        break
    
    if not search_term:
        print("Please enter a search term.\n")
        continue

    needle = search_term.lower()
    
    matching_folders = []
    for label, search_directory, recursive in search_targets:
        if not search_directory.exists():
            continue

        iterator = search_directory.rglob("*") if recursive else search_directory.iterdir()
        for d in iterator:
            if d.is_dir() and needle in d.name.lower():
                if recursive:
                    matching_folders.append((label, str(d.relative_to(search_directory))))
                else:
                    matching_folders.append((label, d.name))
    
    if matching_folders:
        print(f"\nFound {len(matching_folders)} folder(s):")
        for dir_name, folder in matching_folders:
            print(f"  - {dir_name}/{folder}")
    else:
        print(f"No folders found with '{search_term}'")
    
    print()
