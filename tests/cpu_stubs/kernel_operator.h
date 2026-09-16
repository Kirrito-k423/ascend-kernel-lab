#pragma once
#include <cstdint>
#include <cstring>
#define __aicore__
#define __gm__
#define EVENT_ID0 0
using GM_ADDR = uint8_t*;
namespace AscendC {
inline uint64_t tick = (uint64_t(1) << 60);
inline uint32_t block = 0;
inline uint64_t GetSystemCycle() { return tick++; }
inline uint32_t GetBlockIdx() { return block; }
inline uint32_t GetSubBlockIdx() { return 0; }
enum class HardEvent { S_MTE3, MTE3_S };
template<HardEvent> void SetFlag(int) {}
template<HardEvent> void WaitFlag(int) {}
template<typename T> struct LocalTensor {
    T* data;
    void SetValue(uint32_t index, T value) { data[index] = value; }
};
template<typename T> struct GlobalTensor {
    T* data;
    void SetGlobalBuffer(T* ptr) { data = ptr; }
    GlobalTensor operator[](uint32_t offset) { return {data + offset}; }
};
template<typename T> void DataCopy(GlobalTensor<T> dest, LocalTensor<T> src, uint32_t count) {
    std::memcpy(dest.data, src.data, count * sizeof(T));
}
}
