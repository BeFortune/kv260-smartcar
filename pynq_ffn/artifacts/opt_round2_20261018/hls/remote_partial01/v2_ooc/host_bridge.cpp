#include "ffn_accel.hpp"

#if defined(_WIN32)
#define FFN_HOST_EXPORT __declspec(dllexport)
#else
#define FFN_HOST_EXPORT
#endif

// Host validation only. Do not add this wrapper to the HLS synthesis files.
// Caller supplies float32 contiguous buffers and serializes calls.
extern "C" FFN_HOST_EXPORT void ffn_accel_host(
    const float* x, float* y, const float* w1, const float* b1,
    const float* w2, const float* b2, unsigned int num_tokens) {
    ffn_accel(x, y, w1, b1, w2, b2, num_tokens);
}
