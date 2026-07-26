#!/usr/bin/env bash
# 공개 코드 회귀 코퍼스 — 대상 코드베이스와 성격이 같은 실제 코드.
#
# 왜 실제 코드인가: 개발 환경에서 대상 코드를 볼 수 없으므로(설계 §2.2)
# "자동차처럼 생긴" 픽스처를 지어내면 검증했다는 착각만 생긴다.
# 성격이 같은 실제 공개 코드로 대신 검증한다.
#
# 얕은 클론(--depth 1)으로 용량을 줄인다.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)/corpus"
mkdir -p "$DIR"

clone() {  # clone <url> <dir>
  local url="$1" name="$2"
  if [ -d "$DIR/$name" ]; then
    echo "이미 있음: $name"
    return
  fi
  echo "받는 중: $name"
  git clone --depth 1 --quiet "$url" "$DIR/$name"
}

# SOME/IP 구현체 — 대상 도메인과 직결 (C++, 매크로 밀도 높음)
clone https://github.com/COVESA/vsomeip.git vsomeip
# 임베디드 RTOS — #ifdef 변형이 극심한 C
clone https://github.com/zephyrproject-rtos/zephyr.git zephyr
# 포팅 레이어가 매크로 범벅인 C
clone https://github.com/FreeRTOS/FreeRTOS-Kernel.git freertos

echo
echo "완료. tests/corpus/ 아래에 받았습니다."
echo "회귀 실행: python3 -m unittest tests.test_corpus -v"
