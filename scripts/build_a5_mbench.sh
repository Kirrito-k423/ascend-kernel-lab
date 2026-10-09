#!/usr/bin/env bash
set -eo pipefail
MBENCH_DEVICE=${1:-0}
MBENCH_CLOCK_HZ=${2:?请传入有依据的 SYS_CNT Hz}
MBENCH_CLOCK_SOURCE=${3:?请传入时钟依据}
mkdir -p results/setup
python3 -c 'from pathlib import Path;import sys;sys.path.insert(0,"scripts");from run_borrowed_batch import idle;idle(Path("results/setup"),"before")'
cat "$ASCEND_HOME_PATH/aarch64-linux/ascend_toolkit_install.info" > results/setup/cann-install.txt
"$ASCEND_HOME_PATH/bin/bisheng" --version > results/setup/compiler.txt
timeout 180 cmake -S . -B build-a5 -DAKL_NPU_ARCH=dav-3510 -DCMAKE_BUILD_TYPE=Release
timeout 240 cmake --build build-a5 --target akl_datacopy -j2 > results/setup/build-copy.log 2>&1
python3 scripts/prepare_a5_mbench.py --device "$MBENCH_DEVICE" --clock-hz "$MBENCH_CLOCK_HZ" \
    --clock-source "$MBENCH_CLOCK_SOURCE" --output results/setup
timeout 120 python3 scripts/run_datacopy.py --profile results/setup/profile.json --device "$MBENCH_DEVICE" \
    --library build-a5/libakl_datacopy.so --cases results/setup/smoke-cases.json \
    --warmup 1 --samples 2 --output results/alignment-smoke
python3 scripts/plan_alignment.py --output results/alignment-plan
python3 scripts/plan_simt_arithmetic.py --output results/simt-plan
timeout 180 cmake -S examples/a5_mbench -B build-simt -DCMAKE_BUILD_TYPE=Release
timeout 240 cmake --build build-simt -j2 > results/setup/build-simt.log 2>&1
timeout 60 build-simt/akl_simt_arithmetic "$MBENCH_DEVICE" results/simt-plan/smoke.csv 1 2 results/setup/simt-smoke.jsonl
