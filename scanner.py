#!/usr/bin/env python3
"""
Parallel scanner: maps each C++ class/struct name to its definition file.
Threads are used to speed up file reading. Headers are processed first
to maintain canonical definition priority.
"""
import re
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def extract_class_defs(content, filepath):
    """
    Yield (simple_name, filepath) for every real class/struct definition in content.
    Skips forward declarations.
    """
    pattern = rf'(?:\bclass|\bstruct)\s+([\w:]+)\b'
    for match in re.finditer(pattern, content):
        full_name = match.group(1)
        simple_name = full_name.split('::')[-1]

        pos = match.end()
        depth_tmpl = 0
        found_brace = False

        while pos < len(content):
            ch = content[pos]
            if ch == '<':
                depth_tmpl += 1
            elif ch == '>':
                depth_tmpl -= 1
            elif ch == '{' and depth_tmpl == 0:
                found_brace = True
                break
            elif ch == ';' and depth_tmpl == 0:
                break
            pos += 1

        if found_brace:
            # skip over the definition block (brace counting)
            brace_depth = 1
            pos += 1
            while pos < len(content) and brace_depth > 0:
                if content[pos] == '{':
                    brace_depth += 1
                elif content[pos] == '}':
                    brace_depth -= 1
                pos += 1
            yield simple_name, str(filepath)


def process_file(filepath):
    """
    Read a file and return a dict of {class_name: filepath} found in it.
    """
    result = {}
    try:
        content = filepath.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return result
    for name, path in extract_class_defs(content, filepath):
        if name not in result:   # keep first occurrence within the same file
            result[name] = path
    return result


def scan_class_locations_parallel(src_dir, output_json=None, max_workers=8):
    """
    Walk src_dir, process files in parallel, and return a dict
    {class_name: definition_file_path}.
    Headers are processed first to give them priority.
    """
    src_dir = Path(src_dir).expanduser().resolve()
    if not src_dir.exists():
        raise FileNotFoundError(f"Directory not found: {src_dir}")

    # Separate headers and sources
    headers = []
    sources = []
    for ext in ['.h', '.hpp', '.hh', '.hxx']:
        headers.extend(src_dir.rglob(f'*{ext}'))
    for ext in ['.cc', '.cpp', '.cxx']:
        sources.extend(src_dir.rglob(f'*{ext}'))

    print(f"Found {len(headers)} header files, {len(sources)} source files.")

    class_to_file = {}
    lock = threading.Lock()

    def update_mapping(partial_dict):
        with lock:
            for name, path in partial_dict.items():
                if name not in class_to_file:
                    class_to_file[name] = path 
    print("Processing headers...")
    files_iter = headers
    if HAS_TQDM:
        files_iter = tqdm(files_iter, desc="Headers", unit="file")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_file, f): f for f in files_iter}
        for future in as_completed(futures):
            try:
                partial = future.result()
                update_mapping(partial)
            except Exception as e:
                pass   
    print("Processing sources...")
    files_iter = sources
    if HAS_TQDM:
        files_iter = tqdm(files_iter, desc="Sources", unit="file")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_file, f): f for f in files_iter}
        for future in as_completed(futures):
            try:
                partial = future.result()
                update_mapping(partial)
            except Exception:
                pass 
    if output_json:
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(class_to_file, f, indent=2, sort_keys=True)
        print(f"Saved {len(class_to_file)} entries to {output_json}")
    return class_to_file


if __name__ == "__main__":
    SOURCE_DIR = r"C:\Users\Nitro\Desktop\New folder (2)\MDPI_template_ACS - Copy\code\src"
    OUTPUT_JSON = "class_locations.json"
    mapping = scan_class_locations_parallel(SOURCE_DIR, OUTPUT_JSON, max_workers=16)
    print(f"\nFound {len(mapping)} unique class/struct definitions.")
    # print a few examples
    for i, (name, path) in enumerate(mapping.items()):
        if i >= 10:
            break
        print(f"{name:40s} -> {path}")
