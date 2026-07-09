import json
import os
import sys


def sort_manifest(manifest_path):
    with open(manifest_path) as f:
        data = json.load(f)

    ordered_keys = ["domain", "name"]

    new_data = {}
    for key in ordered_keys:
        if key in data:
            new_data[key] = data.pop(key)

    for key in sorted(data.keys()):
        new_data[key] = data[key]

    with open(manifest_path, "w") as f:
        json.dump(new_data, f, indent=2)
        f.write("\n")


def main():
    manifest_path = "custom_components/mozillion/manifest.json"
    if not os.path.exists(manifest_path):
        print(f"Manifest not found at {manifest_path}")
        sys.exit(1)

    sort_manifest(manifest_path)
    print("Manifest sorted successfully.")


if __name__ == "__main__":
    main()
