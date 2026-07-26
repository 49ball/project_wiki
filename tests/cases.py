# -*- coding: utf-8 -*-
"""파서 적합성 케이스 — 현행 파서와 tree-sitter 파서를 같은 자로 잰다.

원칙: 대상 코드베이스를 볼 수 없으므로(설계 §2.2) 여기 케이스는
"자동차처럼 생긴 코드"를 지어낸 것이 아니라, 언어 문법 사실과
공개된 관용구(AUTOSAR RTE 시그니처 형태 등)만 담는다.

symbols: 기대되는 함수 이름 (정렬 비교)
gaps:    반드시 잡혀야 하는 구멍 종류 (부분집합 비교 — 더 잡히는 건 허용)
"""
from collections import namedtuple

Case = namedtuple("Case", "name lang src symbols gaps")

PARSER_CASES = [
    # --- 순수 언어 문법. 지어낸 것이 아니라 C/C++ 사실이다. ---
    Case("평범한 C", "c",
         "int foo(int x)\n{\n  return bar(x);\n}\n",
         ["foo"], set()),
    Case("C++ 한정자", "cpp",
         "void Server::start(int p) {\n  sock_init();\n}\n",
         ["Server::start"], set()),
    Case("템플릿", "cpp",
         "template<typename T>\nT maxv(T a, T b) { return a>b?a:b; }\n",
         ["maxv"], set()),
    Case("람다", "cpp",
         "void f() {\n  auto g = [](int x){ return x*2; };\n}\n",
         ["f"], set()),
    Case("operator()", "cpp",
         "struct S { int operator()(int a) const { return a; } };\n",
         ["operator()"], set()),
    Case("중괄호 초기화", "c",
         "int tbl[] = { 1, 2, 3 };\nint use(void) { return tbl[0]; }\n",
         ["use"], set()),
    Case("Allman 중괄호", "c",
         "int foo(int x)\n{\n  return x;\n}\n",
         ["foo"], set()),
    Case("여러 줄 시그니처", "cpp",
         "static int helper(int a,\n    int b)\n{\n  return a;\n}\n",
         ["helper"], set()),
    Case("주석 속 가짜 함수", "cpp",
         "// void fake() {\nint real() {\n}\n",
         ["real"], set()),
    Case("인클루드 가드", "c",
         "#ifndef FOO_H\n#define FOO_H\nint decl(void);\n#endif\n",
         # 가드는 변형이 아니라 관용구다. 구멍이 나면 안 된다.
         [], set()),

    # --- 여기부터 구멍이 나야 정상인 케이스들 ---
    Case("매크로 감싼 정의", "c",
         "#define STATIC_INLINE static inline\n"
         "STATIC_INLINE int foo(int x) { return x; }\n",
         ["foo"], {"parse_error"}),
    Case("AUTOSAR RTE 시그니처", "c",
         "FUNC(void, RTE_CODE) Rte_Write_Sig(VAR(uint8, AUTOMATIC) v)\n"
         "{\n  send(v);\n}\n",
         ["Rte_Write_Sig"], {"macro_mangled_decl"}),
    Case("토큰 붙이기로 이름 생성", "c",
         "#define DEFINE_HANDLER(s) void handle_##s##_frame(int id)\n"
         "DEFINE_HANDLER(radar) { process(id); }\n",
         # 진짜 이름은 handle_radar_frame 이지만 전개 없이는 알 수 없다.
         # 매크로 이름을 반환하되 반드시 구멍으로 표시해야 한다.
         ["DEFINE_HANDLER"], {"token_paste", "macro_mangled_decl"}),
    Case("함수 포인터 테이블", "c",
         "static handler_t tbl[] = "
         "{ { 0x1A0, handle_can }, { 0x1A4, handle_lin } };\n",
         [], {"fnptr_table"}),
    Case("ifdef 변형 양쪽", "c",
         "#ifdef VARIANT_EU\nint go(void) { return 1; }\n"
         "#else\nint go(void) { return 2; }\n#endif\n",
         # 전처리를 안 하므로 양쪽 다 보인다. 이는 오류가 아니라 변형 열거다.
         ["go", "go"], {"ifdef_branch"}),
]
