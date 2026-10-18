#include "test_support.hpp"

// Small RTL scope only: 1+3+4+5+4+4+4+4+1 = 30 valid tokens,
// nine valid calls plus 0/661/UINT_MAX = twelve transactions; no 659/660 coverage.
int main() {
    try {
        std::vector<float> x(ffn_config::max_tokens * D);
        std::mt19937 rng(20261018);
        fill_random(x, rng, 1.0f);
        const Weights first = random_weights(11);
        const unsigned int counts[] = {1, 3, 4, 5};
        for (unsigned int count : counts) {
            run_case("small-rtl/boundary", count, x, first);
        }
        run_case("small-rtl/reload-B", 4, x, random_weights(22));
        run_case("small-rtl/reload-C", 4, x, random_weights(33));
        Weights zero;
        run_case("small-rtl/zero", 4, x, zero);
        Weights bias;
        for (std::size_t j = 0; j < H; ++j) {
            bias.b1[j] = (static_cast<int>(j % 17) - 8) * 0.75f;
            for (std::size_t k = 0; k < D; ++k) {
                bias.w2[j * D + k] =
                    (static_cast<int>((j + 3 * k) % 13) - 6) * 0.01f;
            }
        }
        for (std::size_t k = 0; k < D; ++k) {
            bias.b2[k] = (static_cast<int>(k % 9) - 4) * 0.025f;
        }
        run_case("small-rtl/bias-exact-erf", 4, x, bias);
        run_case("small-rtl/revisit-A", 1, x, first);
        test_invalid(first);
        std::cout << "All SMALL FFN tests passed: 12 transactions / 30 valid tokens; not full RTL.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
