#ifndef CONV_FRONT_INT8_HPP
#define CONV_FRONT_INT8_HPP
#include <stdint.h>

// Bounds checked by generator: |acc * multiplier| < 2^63; shift in [0,62].
inline int64_t front_round_even(int32_t value, int32_t multiplier, int shift) {
#pragma HLS INLINE
    const int64_t product = int64_t(value) * multiplier;
// Preserve the DSP budget for convolution MACs; requantizers use logic.
#pragma HLS BIND_OP variable=product op=mul impl=fabric
    const uint64_t mag = product < 0 ? uint64_t(-product) : uint64_t(product);
    const uint64_t whole = mag >> shift;
    const uint64_t rem = mag - (whole << shift);
    const uint64_t denom = uint64_t(1) << shift;
    const uint64_t rounded = whole + ((rem * 2 > denom) || ((rem * 2 == denom) && (whole & 1)));
    return product < 0 ? -int64_t(rounded) : int64_t(rounded);
}

static const int FRONT_INPUT_BYTES = 3 * 3 * 66 * 200;
static const int FRONT_OUTPUT_BYTES = 3 * 64 * 9 * 25;
void conv_front_int8(const int8_t *in, int8_t *out);
#endif
