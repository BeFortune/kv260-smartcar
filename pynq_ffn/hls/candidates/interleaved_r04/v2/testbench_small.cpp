#include "test_support.hpp"

// Small RTL only: 1+3+7+8+9+1+1 = 30 valid tokens, seven valid
// calls plus 0/661/UINT_MAX = ten transactions. No 659/660 coverage.
int main() {
    try {
        std::vector<float> x(ffn_config::max_tokens * D);
        std::mt19937 rng(20261018);
        fill_random(x, rng, 1.0f);
        const Weights first = random_weights(11);
        run_case("small-rtl/A", 1, x, first);
        run_case("small-rtl/B-reload", 3, x, random_weights(22));
        run_case("small-rtl/A-revisit", 7, x, first);
        Weights zero;
        run_case("small-rtl/zero", 8, x, zero);
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
        run_case("small-rtl/bias-exact-erf", 9, x, bias);
        run_case("small-rtl/C-reload", 1, x, random_weights(33));
        run_case("small-rtl/A-revisit-final", 1, x, first);
        test_invalid(first);
        std::cout << "All SMALL FFN tests passed: 10 transactions / 30 valid tokens; not full RTL.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
