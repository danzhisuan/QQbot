#!/usr/bin/env bash
# 管理台端到端测试：登录 → 各接口
set -uo pipefail

PORT=18080
BASE="http://127.0.0.1:$PORT"
TOKEN=$(grep '^WEBADMIN_TOKEN=' ~/bot/.env | cut -d= -f2-)
CK=/tmp/admin_ck.txt
rm -f $CK

pass=0; fail=0
chk() { if [ "$2" = "1" ]; then echo "  [OK]   $1"; pass=$((pass+1)); else echo "  [FAIL] $1  ${3:-}"; fail=$((fail+1)); fi; }

echo "=== 0. 未登录访问应被拒 ==="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$BASE/admin")
chk "页面可打开(返回登录页) HTTP=$code" "$([ "$code" = "200" ] && echo 1 || echo 0)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$BASE/admin/api/status")
chk "未登录调接口返回 401" "$([ "$code" = "401" ] && echo 1 || echo 0)" "实际 $code"

echo
echo "=== 1. 错误 Token 应被拒 ==="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X POST "$BASE/admin/api/login" \
  -H 'Content-Type: application/json' -d '{"token":"wrong"}' -c $CK)
chk "错误 Token 返回 401" "$([ "$code" = "401" ] && echo 1 || echo 0)" "实际 $code"

echo
echo "=== 2. 正确 Token 登录 ==="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X POST "$BASE/admin/api/login" \
  -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}" -c $CK)
chk "登录成功" "$([ "$code" = "200" ] && echo 1 || echo 0)" "实际 $code"
grep -q botadmin $CK && chk "Cookie 已下发" 1 || chk "Cookie 已下发" 0

echo
echo "=== 3. 带 Cookie 访问需要认证的页面 ==="
body=$(curl -s --max-time 8 -b $CK "$BASE/admin")
echo "$body" | grep -q "QQ Bot 管理台" && chk "管理页 HTML 正常" 1 || chk "管理页 HTML 正常" 0

echo
echo "=== 4. 各 API 接口 ==="
for ep in status personas config "logs?lines=20"; do
  code=$(curl -s -o /tmp/api_out.json -w '%{http_code}' --max-time 10 -b $CK "$BASE/admin/api/$ep")
  size=$(stat -c %s /tmp/api_out.json)
  chk "/api/$ep HTTP=$code size=$size" "$([ "$code" = "200" ] && [ "$size" -gt 20 ] && echo 1 || echo 0)" "$(head -c 100 /tmp/api_out.json)"
done

echo
echo "=== 5. status 内容预览 ==="
curl -s --max-time 10 -b $CK "$BASE/admin/api/status" | python3 -m json.tool 2>&1 | head -30

echo
echo "=== 6. 连通性测试接口 ==="
echo -n "  测 AI 接口: "
curl -s --max-time 60 -b $CK -X POST "$BASE/admin/api/test/llm" | python3 -c "import sys,json;d=json.load(sys.stdin);print(('OK ' if d.get('ok') else 'FAIL ')+str(d.get('message'))[:110])"
echo -n "  测联网搜索: "
curl -s --max-time 60 -b $CK -X POST "$BASE/admin/api/test/search" | python3 -c "import sys,json;d=json.load(sys.stdin);print(('OK ' if d.get('ok') else 'FAIL ')+str(d.get('message'))[:130])"

echo
echo "=== 7. 人格接口读写测试（不破坏现有数据） ==="
curl -s --max-time 10 -b $CK "$BASE/admin/api/personas" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  人格:', list(d['personas'].keys()))
print('  会话:', d['sessions'] or '（无）')
"

echo
echo "结果: 通过 $pass，失败 $fail"
[ "$fail" = "0" ]
