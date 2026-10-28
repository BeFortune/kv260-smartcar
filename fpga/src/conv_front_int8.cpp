#include "conv_front_int8.hpp"
#include "weights_gen.hpp"

namespace {
inline int8_t saturate(int64_t q) {
#pragma HLS INLINE
    return int8_t(q < -128 ? -128 : (q > 127 ? 127 : q));
}

template<int N> struct SumTree {
    static int32_t sum(const int32_t *p) {
#pragma HLS INLINE
        return SumTree<N/2>::sum(p) + SumTree<N-N/2>::sum(p+N/2);
    }
};
template<> struct SumTree<1> {
    static int32_t sum(const int32_t *p) {
#pragma HLS INLINE
        return p[0];
    }
};

// IC is serial; output lanes and the complete spatial window are parallel.
// A window is fetched once and broadcast to all output-channel lanes.
template<int IC, int OC, int IH, int IW, int K, int LANES, bool SECOND_Q>
void conv_layer(const int8_t x[IC][IH][IW], int8_t y[OC][(IH+1)/2][(IW+1)/2],
                const int8_t w[OC][IC][K][K], const int32_t b[OC],
                const int32_t m[OC], const int32_t s[OC],
                int zin, int zconv, int zrelu, int rm, int rs) {
#pragma HLS INLINE off
#pragma HLS ARRAY_PARTITION variable=w cyclic factor=LANES dim=1
#pragma HLS ARRAY_PARTITION variable=w complete dim=3
#pragma HLS ARRAY_PARTITION variable=w complete dim=4
#pragma HLS ARRAY_PARTITION variable=b cyclic factor=LANES dim=1
#pragma HLS ARRAY_PARTITION variable=m cyclic factor=LANES dim=1
#pragma HLS ARRAY_PARTITION variable=s cyclic factor=LANES dim=1
#pragma HLS BIND_STORAGE variable=w type=rom_1p impl=lutram
    const int OH = (IH+1)/2, OW = (IW+1)/2;
    for (int block = 0; block < OC/LANES; ++block) {
        int32_t acc[LANES];
#pragma HLS ARRAY_PARTITION variable=acc complete
        // Flatten pixel/channel traversal: drain the pipeline per channel block,
        // not per pixel. Requantization occurs only on the last input channel.
        for (int step = 0; step < OH*OW*IC; ++step) {
#pragma HLS PIPELINE II=1
            const int ic = step % IC;
            const int pixel = step / IC;
            const int oy = pixel / OW, ox = pixel % OW;
            int16_t window[K*K];
#pragma HLS ARRAY_PARTITION variable=window complete
            for (int ky = 0; ky < K; ++ky) {
#pragma HLS UNROLL
                for (int kx = 0; kx < K; ++kx) {
#pragma HLS UNROLL
                    const int iy = 2*oy+ky-K/2, ix = 2*ox+kx-K/2;
                    // Padding is real zero: x_quantized == zin.
                    window[ky*K+kx] = (iy >= 0 && iy < IH && ix >= 0 && ix < IW)
                        ? int16_t(int(x[ic][iy][ix])-zin) : int16_t(0);
                }
            }
            for (int lane = 0; lane < LANES; ++lane) {
#pragma HLS UNROLL
                const int oc = block*LANES+lane;
                int32_t products[K*K];
#pragma HLS ARRAY_PARTITION variable=products complete
                for (int ky = 0; ky < K; ++ky) {
#pragma HLS UNROLL
                    for (int kx = 0; kx < K; ++kx) {
#pragma HLS UNROLL
                        products[ky*K+kx] = int32_t(window[ky*K+kx]) * w[oc][ic][ky][kx];
                    }
                }
                const int32_t next = (ic == 0 ? b[oc] : acc[lane]) + SumTree<K*K>::sum(products);
                acc[lane] = next;
                if (ic == IC-1) {
                    const int8_t cq = saturate(front_round_even(next, m[oc], s[oc])+zconv);
                    const int32_t rq = cq < zconv ? zconv : int32_t(cq);
                    y[oc][oy][ox] = SECOND_Q ? saturate(front_round_even(rq-zconv, rm, rs)+zrelu) : int8_t(rq);
                }
            }
        }
    }
}
}

void conv_front_int8(const int8_t *in, int8_t *out) {
#pragma HLS INTERFACE m_axi port=in offset=slave bundle=gmem_in depth=118800
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem_out depth=43200
#pragma HLS INTERFACE s_axilite port=in bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    using namespace front_params;
    static int8_t input[3][66][200];
    static int8_t f0[24][33][100];
    static int8_t f1[48][17][50];
    static int8_t f2[64][9][25];
#pragma HLS ARRAY_PARTITION variable=input cyclic factor=5 dim=2
#pragma HLS ARRAY_PARTITION variable=input cyclic factor=5 dim=3
#pragma HLS ARRAY_PARTITION variable=f0 cyclic factor=16 dim=1
#pragma HLS ARRAY_PARTITION variable=f0 cyclic factor=3 dim=2
#pragma HLS ARRAY_PARTITION variable=f0 cyclic factor=3 dim=3
#pragma HLS ARRAY_PARTITION variable=f1 cyclic factor=16 dim=1
#pragma HLS ARRAY_PARTITION variable=f1 cyclic factor=3 dim=2
#pragma HLS ARRAY_PARTITION variable=f1 cyclic factor=3 dim=3
#pragma HLS ARRAY_PARTITION variable=f2 cyclic factor=16 dim=1
// Many shallow banks provide simultaneous lane writes and window reads.
// Force distributed RAM for f0/f1, avoiding one BRAM per tiny bank.
#pragma HLS BIND_STORAGE variable=input type=ram_1p impl=bram
#pragma HLS BIND_STORAGE variable=f0 type=ram_1p impl=lutram
#pragma HLS BIND_STORAGE variable=f1 type=ram_1p impl=lutram
#pragma HLS BIND_STORAGE variable=f2 type=ram_1p impl=bram
    for (int camera = 0; camera < 3; ++camera) {
        for (int c = 0; c < 3; ++c)
            for (int y = 0; y < 66; ++y)
                for (int x = 0; x < 200; ++x) {
#pragma HLS PIPELINE II=1
                    input[c][y][x] = in[((camera*3+c)*66+y)*200+x];
                }
        conv_layer<3,24,66,200,5,2,true>(input, f0, w0, b0, m0, s0, zin0, zconv0, zrelu0, rm0, rs0);
        conv_layer<24,48,33,100,3,16,true>(f0, f1, w1, b1, m1, s1, zin1, zconv1, zrelu1, rm1, rs1);
        conv_layer<48,64,17,50,3,16,false>(f1, f2, w2, b2, m2, s2, zin2, zconv2, zrelu2, rm2, rs2);
        for (int c = 0; c < 64; ++c)
            for (int y = 0; y < 9; ++y)
                for (int x = 0; x < 25; ++x) {
#pragma HLS PIPELINE II=1
                    out[((camera*64+c)*9+y)*25+x] = f2[c][y][x];
                }
    }
}
