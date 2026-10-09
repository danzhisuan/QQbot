#!/usr/bin/env bash
# 管理台写操作 + 重启测试
set -uo pipefail

BASE="http://127.0.0.1:18080"
TOKEN=$(grep '^WEBADMIN_TOKEN=' ~/bot/.env | cut -d= -f2-)
CK=/tmp/admin_ck2.txt
rm -f $CK
curl -s -o /dev/null -X POST "$BASE/admin/api/login" \
  -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}" -c $CK

pass=0; fail=0
chk() { if [ "$2" = "1" ]; then echo "  [OK]   $1"; pass=$((pass+1)); else echo "  [FAIL] $1  ${3:-}"; fail=$((fail+1)); fi; }

echo "=== 1. 人格文件原样保存（幂等，不该改变内容） ==="
curl -s -b $CK "$BASE/admin/api/personas" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps({'personas': d['personas']}, ensure_ascii=False))" > /tmp/payload.json
BEFORE=$(curl -s -b $CK "$BASE/admin/api/personas" | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin)['personas'],sort_keys=True,ensure_ascii=False))")
resp=$(curl -s -b $CK -X POST "$BASE/admin/api/personas" -H 'Content-Type: application/json' -d @/tmp/payload.json)
echo "  保存响应: $resp"
AFTER=$(curl -s -b $CK "$BASE/admin/api/personas" | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin)['personas'],sort_keys=True,ensure_ascii=False))")
chk "原样保存后内容不变" "$([ "$BEFORE" = "$AFTER" ] && echo 1 || echo 0)"

echo
echo "=== 2. 拒绝空人格集（保护性校验） ==="
code=$(curl -s -o /tmp/e.json -w '%{http_code}' -b $CK -X POST "$BASE/admin/api/personas" \
  -H 'Content-Type: application/json' -d '{"personas":{}}')
chk "空人格集返回 400" "$([ "$code" = "400" ] && echo 1 || echo 0)" "实际 $code  $(cat /tmp/e.json)"

echo
echo "=== 3. 拒绝没有 prompt 的人格 ==="
code=$(curl -s -o /tmp/e.json -w '%{http_code}' -b $CK -X POST "$BASE/admin/api/personas" \
  -H 'Content-Type: application/json' -d '{"personas":{"坏人格":{"description":"没有提示词"}}}')
chk "无 prompt 返回 400" "$([ "$code" = "400" ] && echo 1 || echo 0)" "实际 $code  $(cat /tmp/e.json)"

echo
echo "=== 4. 切换会话人格 → 再切回 ==="
OLD=$(curl -s -b $CK "$BASE/admin/api/personas" | python3 -c "
import sys,json; d=json.load(sys.stdin)['sessions']; print(d.get('private:100000000',''))")
echo "  切换前: private:100000000 = '${OLD:-（未设置）}'"

curl -s -b $CK -X POST "$BASE/admin/api/session" -H 'Content-Type: application/json' \
  -d '{"session":"private:100000000","persona":"猫娘"}' > /dev/null
NEW=$(curl -s -b $CK "$BASE/admin/api/personas" | python3 -c "
import sys,json; print(json.load(sys.stdin)['sessions'].get('private:100000000',''))")
chk "已切到 猫娘" "$([ "$NEW" = "猫娘" ] && echo 1 || echo 0)" "实际 '$NEW'"

# 还原
if [ -n "$OLD" ]; then
  curl -s -b $CK -X POST "$BASE/admin/api/session" -H 'Content-Type: application/json' \
    -d "{\"session\":\"private:100000000\",\"persona\":\"$OLD\"}" > /dev/null
else
  curl -s -b $CK -X POST "$BASE/admin/api/session" -H 'Content-Type: application/json' \
    -d '{"session":"private:100000000","persona":""}' > /dev/null
fi
BACK=$(curl -s -b $CK "$BASE/admin/api/personas" | python3 -c "
import sys,json; print(json.load(sys.stdin)['sessions'].get('private:100000000',''))")
chk "已还原为 '${OLD:-（未设置）}'" "$([ "$BACK" = "$OLD" ] && echo 1 || echo 0)" "实际 '$BACK'"

echo
echo "=== 5. 切换到不存在的人格应被拒 ==="
code=$(curl -s -o /tmp/e.json -w '%{http_code}' -b $CK -X POST "$BASE/admin/api/session" \
  -H 'Content-Type: application/json' -d '{"session":"private:1","persona":"不存在的人格"}')
chk "不存在的人格返回 400" "$([ "$code" = "400" ] && echo 1 || echo 0)" "实际 $code"

echo
echo "=== 6. 重启接口（真实测试） ==="
t0=$(date +%s)
curl -s -b $CK -X POST "$BASE/admin/api/restart" | head -c 80
echo
echo "  等待容器重启..."
for i in $(seq 1 30); do
  sleep 2
  if curl -s -o /dev/null --max-time 4 "$BASE/admin" 2>/dev/null; then
    t1=$(date +%s)
    echo "  ✅ 管理台已恢复，耗时 $((t1 - t0)) 秒"
    break
  fi
done

sleep 8
echo "  --- 重启后状态 ---"
rm -f $CK
curl -s -o /dev/null -X POST "$BASE/admin/api/login" -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\"}" -c $CK
curl -s -b $CK "$BASE/admin/api/status" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('    uptime :', d['uptime'])
print('    bots   :', d['bots'])
print('    plugins:', d['plugins'])
"
chk "重启后 QQ 连接恢复" "$(curl -s -b $CK "$BASE/admin/api/status" | python3 -c "
import sys,json; sys.exit(0 if json.load(sys.stdin)['bots'] else 1)" && echo 1 || echo 0)"

echo
echo "结果: 通过 $pass，失败 $fail"
[ "$fail" = "0" ]
