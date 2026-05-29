import subprocess
import tempfile
import os
import re
import time
import random
from collections import OrderedDict
from typing import Dict, Optional

# ----------------------------------------------------------------------
# 1. C++ benchmark source code (as a string)
# ----------------------------------------------------------------------
CPP_BENCHMARK_CODE = '''
#include <iostream>
#include <chrono>
#include <map>
#include <unordered_map>
#include <vector>
#include <string>
#include <algorithm>
#include <random>

class SavedFilesService {
public:
    struct SavedFileEntry {
        std::string id;
        std::string file_path;
        bool is_directory;
    };

    void RegisterFileEntry(const std::string& extension_id,
                           const std::string& id,
                           const std::string& file_path,
                           bool is_directory) {
        auto& saved = extension_map_[extension_id];
        saved.entries[id] = {id, file_path, is_directory};
        if (saved.lru.find(id) == saved.lru.end()) {
            saved.lru[id] = 0;
        }
    }

    void EnqueueFileEntry(const std::string& extension_id,
                          const std::string& id) {
        auto it = extension_map_.find(extension_id);
        if (it != extension_map_.end()) {
            auto& saved = it->second;
            auto lru_it = saved.lru.find(id);
            if (lru_it != saved.lru.end()) {
                saved.lru.erase(lru_it);
                saved.lru[id] = ++saved.sequence_counter;
            }
        }
    }

    bool IsRegistered(const std::string& extension_id,
                      const std::string& id) {
        auto it = extension_map_.find(extension_id);
        if (it == extension_map_.end()) return false;
        return it->second.entries.find(id) != it->second.entries.end();
    }

    bool GetFileEntry(const std::string& extension_id,
                      const std::string& id,
                      SavedFileEntry& out_entry) {
        auto it = extension_map_.find(extension_id);
        if (it == extension_map_.end()) return false;
        auto entry_it = it->second.entries.find(id);
        if (entry_it == it->second.entries.end()) return false;
        out_entry = entry_it->second;
        return true;
    }

    std::vector<SavedFileEntry> GetAllFileEntries(const std::string& extension_id) {
        std::vector<SavedFileEntry> result;
        auto it = extension_map_.find(extension_id);
        if (it != extension_map_.end()) {
            for (const auto& pair : it->second.entries) {
                result.push_back(pair.second);
            }
        }
        return result;
    }

    void ClearQueue(const std::string& extension_id) {
        extension_map_.erase(extension_id);
    }

    void OnApplicationTerminating() {
        extension_map_.clear();
    }

private:
    struct SavedFiles {
        std::unordered_map<std::string, SavedFileEntry> entries;
        std::map<std::string, uint64_t> lru;
        uint64_t sequence_counter = 0;
    };
    std::map<std::string, SavedFiles> extension_map_;
};

template <typename Func>
double measure_worst_us(Func&& func, int iterations = 100) {
    double worst_us = 0.0;
    for (int i = 0; i < iterations; ++i) {
        auto start = std::chrono::high_resolution_clock::now();
        func();
        auto end = std::chrono::high_resolution_clock::now();
        double elapsed_us = std::chrono::duration<double, std::micro>(end - start).count();
        if (elapsed_us > worst_us) worst_us = elapsed_us;
    }
    return worst_us;
}

int main() {
    const int NUM_FILES = 2000;
    const int NUM_ENQUEUE_OPS = 5000;
    const int MEASURE_RUNS = 50;

    SavedFilesService service;
    std::string ext_id = "test_extension";

    std::vector<std::string> file_ids(NUM_FILES);
    for (int i = 0; i < NUM_FILES; ++i) {
        file_ids[i] = std::to_string(i);
    }

    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dist(0, NUM_FILES - 1);

    // Register all files (worst-case creation)
    double worst_register_total = measure_worst_us([&]() {
        SavedFilesService tmp;
        for (int i = 0; i < NUM_FILES; ++i) {
            tmp.RegisterFileEntry(ext_id, file_ids[i], file_ids[i], false);
        }
    }, MEASURE_RUNS);
    double worst_register_per_call = worst_register_total / NUM_FILES;
    std::cout << "Worst-case RegisterFileEntry (all " << NUM_FILES << " files): "
              << worst_register_total << " us total, "
              << worst_register_per_call << " us per call\\n";

    // Populate service for subsequent tests
    for (const auto& fid : file_ids) {
        service.RegisterFileEntry(ext_id, fid, fid, false);
    }

    // Enqueue random files
    double worst_enqueue_total = measure_worst_us([&]() {
        SavedFilesService tmp2;
        for (int i = 0; i < NUM_FILES; ++i) {
            tmp2.RegisterFileEntry(ext_id, file_ids[i], file_ids[i], false);
        }
        for (int i = 0; i < NUM_ENQUEUE_OPS; ++i) {
            int idx = dist(gen);
            tmp2.EnqueueFileEntry(ext_id, file_ids[idx]);
        }
    }, MEASURE_RUNS);
    double worst_enqueue_per_op = worst_enqueue_total / NUM_ENQUEUE_OPS;
    std::cout << "Worst-case EnqueueFileEntry (" << NUM_ENQUEUE_OPS << " ops): "
              << worst_enqueue_total << " us total, "
              << worst_enqueue_per_op << " us per op\\n";

    // IsRegistered (missing + last)
    double worst_isregistered = measure_worst_us([&]() {
        service.IsRegistered(ext_id, "nonexistent_id_xyz");
        service.IsRegistered(ext_id, file_ids.back());
    }, MEASURE_RUNS);
    std::cout << "Worst-case IsRegistered (2 lookups): " << worst_isregistered
              << " us (" << worst_isregistered/2 << " us per lookup)\\n";

    // GetFileEntry
    SavedFilesService::SavedFileEntry dummy;
    double worst_get = measure_worst_us([&]() {
        service.GetFileEntry(ext_id, "nonexistent", dummy);
        service.GetFileEntry(ext_id, file_ids.back(), dummy);
    }, MEASURE_RUNS);
    std::cout << "Worst-case GetFileEntry (2 lookups): " << worst_get
              << " us (" << worst_get/2 << " us per lookup)\\n";

    // GetAllFileEntries
    double worst_getall = measure_worst_us([&]() {
        auto all = service.GetAllFileEntries(ext_id);
        if (all.empty()) std::cout << "";
    }, MEASURE_RUNS);
    std::cout << "Worst-case GetAllFileEntries: " << worst_getall << " us\\n";

    // ClearQueue
    double worst_clear = measure_worst_us([&]() {
        SavedFilesService tmp3;
        for (int i = 0; i < NUM_FILES; ++i) {
            tmp3.RegisterFileEntry(ext_id, file_ids[i], file_ids[i], false);
        }
        tmp3.ClearQueue(ext_id);
    }, MEASURE_RUNS);
    std::cout << "Worst-case ClearQueue: " << worst_clear << " us\\n";

    // OnApplicationTerminating
    double worst_term = measure_worst_us([&]() {
        SavedFilesService tmp4;
        for (int i = 0; i < NUM_FILES; ++i) {
            tmp4.RegisterFileEntry(ext_id, file_ids[i], file_ids[i], false);
        }
        tmp4.OnApplicationTerminating();
    }, MEASURE_RUNS);
    std::cout << "Worst-case OnApplicationTerminating: " << worst_term << " us\\n";

    return 0;
}
'''

# ----------------------------------------------------------------------
# 2. Helper: compile and run C++ benchmark with static linking
# ----------------------------------------------------------------------
def run_cpp_benchmark(cpp_code: str,
                      compiler_path: str = r"C:\llvm-mingw\bin\clang++.exe",
                      compile_flags: list = None,
                      runs: int = 3,
                      timeout_sec: int = 120) -> Optional[Dict[str, float]]:
    """
    Compiles the given C++ code with static linking and runs the executable.
    Returns a dict of worst‑case times in microseconds.
    """
    if compile_flags is None:
        # -static links all runtime libraries (libunwind, libc++, etc.) into the executable
        compile_flags = ['-std=c++17', '-O2', '-static']

    # Check if compiler exists
    if not os.path.exists(compiler_path):
        print(f"Compiler not found at: {compiler_path}")
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "benchmark.cpp")
        exe_path = os.path.join(tmpdir, "benchmark.exe" if os.name == "nt" else "benchmark")

        # Write source file
        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(cpp_code)

        # Compile
        compile_cmd = [compiler_path] + compile_flags + [src_path, '-o', exe_path]
        try:
            result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=timeout_sec)
            if result.returncode != 0:
                print("Compilation failed:")
                print(result.stderr)
                return None
        except Exception as e:
            print(f"Compilation error: {e}")
            return None

        # Run the executable multiple times
        all_results = {}
        pattern = re.compile(r"Worst-case ([\w]+).*?: ([\d.]+) us")

        for run_idx in range(runs):
            try:
                run_result = subprocess.run([exe_path], capture_output=True, text=True, timeout=timeout_sec)
                if run_result.returncode != 0:
                    print(f"Run {run_idx+1} failed: {run_result.stderr}")
                    continue
                for line in run_result.stdout.splitlines():
                    m = pattern.search(line)
                    if m:
                        op_name = m.group(1)
                        val = float(m.group(2))
                        if op_name not in all_results or val > all_results[op_name]:
                            all_results[op_name] = val
            except Exception as e:
                print(f"Run {run_idx+1} error: {e}")
                continue

        return all_results if all_results else None
 
# ----------------------------------------------------------------------
# 4. Main 
# ----------------------------------------------------------------------
def main():
    print("=== WCET Measurement for SavedFilesService ===\n")
    print("Trying to compile and run C++ benchmark (static linking)...")
    cpp_results = run_cpp_benchmark(CPP_BENCHMARK_CODE, runs=3)

    if cpp_results:
        print("\n✅ C++ benchmark succeeded. Worst‑case times (microseconds):\n")
        for op, val in cpp_results.items():
            print(f"  {op}: {val:.2f} µs")
  

if __name__ == "__main__":
    main()
