#!/usr/bin/env bash
# 插件商店端到端测试（在服务器上执行）
set -uo pipefail

BASE="http://127.0.0.1:18080"
TOKEN=$(grep '^WEBADMIN_TOKEN=' ~/bot/.env | cut -d= -f2-)
CK=/tmp/store_ck.txt
rm -f $CK
curl -s -o /dev/null -X POST "$BASE/admin/api/login" \
  -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}" -c $CK

pass=0; fail=0
chk() { if [ "$2" = "1" ]; then echo "  [OK]   $1"; pass=$((pass+1)); else echo "  [FAIL] $1  ${3:-}"; fail=$((fail+1)); fi; }

echo "=== 1. 拉取插件索引（首次约 1-2 秒） ==="
t0=$(date +%s)
code=$(curl -s -o /tmp/store.json -w '%{http_code}' --max-time 60 -b $CK "$BASE/admin/api/store?q=&page=0")
t1=$(date +%s)
chk "索引拉取 HTTP=$code 耗时 $((t1-t0))s" "$([ "$code" = "200" ] && echo 1 || echo 0)" "$(head -c 120 /tmp/store.json)"
python3 -c "
import json
d = json.load(open('/tmp/store.json', encoding='utf-8'))
print(f\"  索引总数: {d['total_registry']}  本次匹配: {d['total']}  返回: {len(d['items'])}\")
print(f\"  标签数: {len(d['tags'])}  已装: {d['installed']}\")
"

echo
echo "=== 2. 搜索功能 ==="
for q in "签到" "天气" "点歌"; do
  curl -s --max-time 30 -b $CK "$BASE/admin/api/store?q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote('$q'))")" -o /tmp/s.json
  python3 -c "
import json
d = json.load(open('/tmp/s.json', encoding='utf-8'))
names = [i['name'] for i in d['items'][:3]]
print(f\"  搜「$q」→ 匹配 {d['total']} 个，前 3: {names}\")
"
done

echo
echo "=== 3. 标签筛选 ==="
curl -s --max-time 30 -b $CK "$BASE/admin/api/store?tag=server" -o /tmp/s.json
python3 -c "
import json
d = json.load(open('/tmp/s.json', encoding='utf-8'))
print(f\"  标签 server → {d['total']} 个\")
"

echo
echo "=== 4. 已装列表 ==="
curl -s --max-time 30 -b $CK "$BASE/admin/api/store/installed" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  已装:', [i['module_name'] for i in d['items']] or '（无）')
print('  安装目录:', d['libs_dir'])
"

echo
echo "=== 5. 真实安装一个插件（nonebot-plugin-status） ==="
echo -n "  发送安装请求… "
t0=$(date +%s)
code=$(curl -s -o /tmp/inst.json -w '%{http_code}' --max-time 300 -b $CK \
  -X POST "$BASE/admin/api/store/install" -H 'Content-Type: application/json' \
  -d '{"module_name":"nonebot_plugin_status","project_link":"nonebot-plugin-status"}')
t1=$(date +%s)
echo "HTTP=$code 耗时 $((t1-t0))s"
python3 -c "
import json
d = json.load(open('/tmp/inst.json', encoding='utf-8'))
print('  结果:', ('✅ ' if d.get('ok') else '❌ ') + str(d.get('message'))[:150])
print('  热加载:', d.get('loaded'), ' 需重启:', d.get('needs_restart'))
"
chk "插件安装成功" "$(python3 -c "import json;print(1 if json.load(open('/tmp/inst.json'))['ok'] else 0)")"

echo
echo "=== 6. 安装后的状态 ==="
curl -s --max-time 30 -b $CK "$BASE/admin/api/store/installed" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for i in d['items']:
    print(f\"  - {i['module_name']}  文件存在={i['files_present']}  在索引里={i['in_registry']}\")
"
echo "  卷体积: $(du -sh ~/bot/data/bot/pylibs 2>/dev/null | cut -f1)"
echo "  清单文件: $(cat ~/bot/data/bot/extra_plugins.json 2>/dev/null || echo '不存在')"

echo
echo "=== 7. 重启容器，验证持久化 ==="
docker compose -f ~/bot/docker-compose.yml up -d --build nonebot > /dev/null 2>&1
sleep 18
docker logs --tail 40 nonebot 2>&1 | grep -E "第三方插件|Succeeded to load plugin" | tail -n 8

echo
echo "=== 8. 卸载 ==="
code=$(curl -s -o /tmp/un.json -w '%{http_code}' --max-time 60 -b $CK \
  -X POST "$BASE/admin/api/store/uninstall" -H 'Content-Type: application/json' \
  -d '{"module_name":"nonebot_plugin_status","delete_files":true}')
python3 -c "
import json
d = json.load(open('/tmp/un.json', encoding='utf-8'))
print('  结果:', str(d.get('message'))[:150])
print('  清单现在:', open('/home/botuser/bot/data/bot/extra_plugins.json').read().strip())
"
chk "卸载接口正常" "$([ "$code" = "200" ] && echo 1 || echo 0)"

echo
echo "结果: 通过 $pass，失败 $fail"
[ "$fail" = "0" ]
