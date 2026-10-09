#include "kernel_operator.h"
#include "simt_api/asc_simt.h"
using namespace AscendC;

template<int Threads, int Op>
__simt_vf__ __aicore__ LAUNCH_BOUND(Threads) inline void ArithmeticVF(
    __ubuf__ float* values, __ubuf__ float* rhs, uint32_t n, uint32_t steps) {
    for (uint32_t i = threadIdx.x; i < n; i += Threads) {
        float x = values[i];
        const float operand = rhs[i];
        for (uint32_t j = 0; j < steps; ++j) {
            if constexpr (Op == 0) x += operand;
            else if constexpr (Op == 1) x -= operand;
            else if constexpr (Op == 2) x *= operand;
            else x /= operand;
        }
        values[i] = x;
    }
}

template<int Threads>
__simt_vf__ __aicore__ LAUNCH_BOUND(Threads) inline void TouchVF(
    __ubuf__ volatile float* values, uint32_t stamp) {
    // 可观察最小线程体；报告明确包括每线程一次 UB 写出。
    values[threadIdx.x] = static_cast<float>(stamp + threadIdx.x);
}

__aicore__ inline void VectorComplete() {
    SetFlag<HardEvent::V_S>(EVENT_ID0);
    WaitFlag<HardEvent::V_S>(EVENT_ID0);
}

template<int Op>
__aicore__ inline void ScalarWork(LocalTensor<float> values, LocalTensor<float> rhs,
                                uint32_t n, uint32_t steps) {
    for (uint32_t i = 0; i < n; ++i) {
        float v = values.GetValue(i), operand = rhs.GetValue(i);
        for (uint32_t j = 0; j < steps; ++j) {
            if constexpr (Op == 0) v += operand;
            else if constexpr (Op == 1) v -= operand;
            else if constexpr (Op == 2) v *= operand;
            else v /= operand;
        }
        values.SetValue(i, v);
    }
}

template<int Op>
__aicore__ inline void SIMDWork(LocalTensor<float> values, LocalTensor<float> rhs,
                              uint32_t n, uint32_t steps) {
    for (uint32_t j = 0; j < steps; ++j) {
        if constexpr (Op == 0) Add(values, values, rhs, n);
        else if constexpr (Op == 1) Sub(values, values, rhs, n);
        else if constexpr (Op == 2) Mul(values, values, rhs, n);
        else Div(values, values, rhs, n);
        PipeBarrier<PIPE_V>();
    }
    VectorComplete();
}

template<int Threads, int Op>
__aicore__ inline void Invoke(LocalTensor<float> values, LocalTensor<float> rhs,
                             uint32_t n, uint32_t steps) {
    Simt::VF_CALL<ArithmeticVF<Threads, Op>>(Simt::Dim3{Threads, 1, 1},
        reinterpret_cast<__ubuf__ float*>(values.GetPhyAddr()),
        reinterpret_cast<__ubuf__ float*>(rhs.GetPhyAddr()), n, steps);
}

template<int Threads>
__aicore__ inline void DispatchSIMT(uint32_t op, LocalTensor<float> values,
                                  LocalTensor<float> rhs, uint32_t n, uint32_t steps) {
    switch (op) {
        case 0: Invoke<Threads, 0>(values, rhs, n, steps); break;
        case 1: Invoke<Threads, 1>(values, rhs, n, steps); break;
        case 2: Invoke<Threads, 2>(values, rhs, n, steps); break;
        case 3: Invoke<Threads, 3>(values, rhs, n, steps); break;
    }
}

template<int Threads>
__aicore__ inline void Work(uint32_t impl, uint32_t op, LocalTensor<float> values,
                          LocalTensor<float> rhs, uint32_t n, uint32_t steps, uint32_t calls) {
    if (impl == 1) {
        DispatchSIMT<Threads>(op, values, rhs, n, steps);
        VectorComplete();
    } else if (impl == 3) {
        for (uint32_t j = 0; j < calls; ++j) {
            Simt::VF_CALL<TouchVF<Threads>>(Simt::Dim3{Threads, 1, 1},
                reinterpret_cast<__ubuf__ volatile float*>(values.GetPhyAddr()), j);
            VectorComplete();
        }
    } else if (impl == 4) {
        for (uint32_t j = 0; j < calls; ++j) {
            asm volatile("" ::: "memory");
            VectorComplete();
        }
    }
}

extern "C" __global__ __aicore__ void arithmetic_kernel(
    GM_ADDR input, GM_ADDR operands, GM_ADDR output, GM_ADDR timer,
    uint32_t impl, uint32_t op, uint32_t n, uint32_t threads, uint32_t steps, uint32_t calls) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    TPipe pipe;
    TBuf<TPosition::VECCALC> valuesBuf, rhsBuf, timerBuf;
    pipe.InitBuffer(valuesBuf, n * sizeof(float));
    pipe.InitBuffer(rhsBuf, n * sizeof(float));
    pipe.InitBuffer(timerBuf, 32);
    auto values = valuesBuf.Get<float>();
    auto rhs = rhsBuf.Get<float>();
    auto time = timerBuf.Get<uint64_t>();
    GlobalTensor<float> x, y, out;
    GlobalTensor<uint64_t> ticks;
    x.SetGlobalBuffer(reinterpret_cast<__gm__ float*>(input));
    y.SetGlobalBuffer(reinterpret_cast<__gm__ float*>(operands));
    out.SetGlobalBuffer(reinterpret_cast<__gm__ float*>(output));
    ticks.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(timer));
    DataCopy(values, x, n);
    DataCopy(rhs, y, n);
    SetFlag<HardEvent::MTE2_V>(EVENT_ID0); WaitFlag<HardEvent::MTE2_V>(EVENT_ID0);
    SetFlag<HardEvent::MTE2_S>(EVENT_ID0); WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    const uint64_t start = GetSystemCycle();
    if (impl == 0) {
        switch (op) {
            case 0: ScalarWork<0>(values, rhs, n, steps); break;
            case 1: ScalarWork<1>(values, rhs, n, steps); break;
            case 2: ScalarWork<2>(values, rhs, n, steps); break;
            case 3: ScalarWork<3>(values, rhs, n, steps); break;
        }
    } else if (impl == 2) {
        switch (op) {
            case 0: SIMDWork<0>(values, rhs, n, steps); break;
            case 1: SIMDWork<1>(values, rhs, n, steps); break;
            case 2: SIMDWork<2>(values, rhs, n, steps); break;
            case 3: SIMDWork<3>(values, rhs, n, steps); break;
        }
    } else {
        switch (threads) {
#define THREAD_CASE(N) case N: Work<N>(impl, op, values, rhs, n, steps, calls); break
            THREAD_CASE(1); THREAD_CASE(8); THREAD_CASE(16); THREAD_CASE(32);
            THREAD_CASE(64); THREAD_CASE(128); THREAD_CASE(256); THREAD_CASE(512);
            THREAD_CASE(1024); THREAD_CASE(2048);
#undef THREAD_CASE
        }
    }
    const uint64_t end = GetSystemCycle();
    time.SetValue(0, start); time.SetValue(1, end);
    time.SetValue(2, 0x414b4c53494d5431ULL); time.SetValue(3, n);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0); WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    SetFlag<HardEvent::V_MTE3>(EVENT_ID0); WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    DataCopy(out, values, n);
    DataCopy(ticks, time, 4);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0); WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}

extern "C" void launch_arithmetic(void* stream, void* x, void* rhs, void* out, void* ticks,
    uint32_t impl, uint32_t op, uint32_t n, uint32_t threads, uint32_t steps, uint32_t calls) {
    arithmetic_kernel<<<1, nullptr, stream>>>(static_cast<uint8_t*>(x), static_cast<uint8_t*>(rhs),
        static_cast<uint8_t*>(out), static_cast<uint8_t*>(ticks), impl, op, n, threads, steps, calls);
}
