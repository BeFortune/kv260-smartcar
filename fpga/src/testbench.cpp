#include "conv_front_int8.hpp"
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#ifndef FRONT_VECTOR_DIR
#define FRONT_VECTOR_DIR "."
#endif

bool read_exact(const std::string &path, std::vector<int8_t> &v) {
    std::ifstream f(path.c_str(), std::ios::binary);
    if (!f || !f.read(reinterpret_cast<char *>(&v[0]), v.size())) {
        std::cerr << "missing/short binary: " << path << "\n";
        return false;
    }
    char extra;
    if (f.get(extra)) {
        std::cerr << "oversized binary: " << path << "\n";
        return false;
    }
    return true;
}

int main(int argc, char **argv) {
    if (argc > 2) return 2;
    // Same rounding helper as the kernel; includes negative and positive ties.
    const int v[] = {-7,-5,-3,-1,0,1,3,5,7};
    const int expected[] = {-4,-2,-2,0,0,0,2,2,4};
    for (int i = 0; i < 9; ++i)
        if (front_round_even(v[i], 1, 1) != expected[i] || front_round_even(v[i], 1, 0) != v[i]) return 3;
    const std::string dir = argc == 2 ? argv[1] : FRONT_VECTOR_DIR;
    std::ifstream manifest((dir+"/cases.txt").c_str());
    if (!manifest) { std::cerr << "cannot open cases.txt in " << dir << "\n"; return 2; }
    std::vector<int8_t> input(FRONT_INPUT_BYTES), golden(FRONT_OUTPUT_BYTES), output(FRONT_OUTPUT_BYTES);
    std::string ip, op;
    size_t cases = 0, differences = 0;
    while (manifest >> ip) {
        if (!(manifest >> op)) return 2;
        if (!read_exact(dir+"/"+ip, input) || !read_exact(dir+"/"+op, golden)) return 2;
        conv_front_int8(&input[0], &output[0]);
        size_t bad = 0;
        for (size_t i = 0; i < output.size(); ++i) {
            if (output[i] != golden[i]) {
                if (bad < 5) std::cerr << ip << " index " << i << " got " << int(output[i]) << " expected " << int(golden[i]) << "\n";
                ++bad;
            }
        }
        std::cout << ip << " differences=" << bad << "/" << output.size() << "\n";
        differences += bad;
        ++cases;
    }
    std::cout << "cases=" << cases << " elements=" << cases*output.size() << " mismatches=" << differences << "\n";
    return cases && !differences ? 0 : 1;
}
