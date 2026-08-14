#!/bin/zsh

set -u
unsetopt BG_NICE

medgate_project_dir="${0:A:h}"
medgate_app_url="http://127.0.0.1:8000/"
medgate_health_url="${medgate_app_url}health"
medgate_server_pid=""

cd "$medgate_project_dir" || exit 1

medgate_pause() {
  if [[ -t 0 ]]; then
    echo
    read -r "medgate_pause_reply?按回车键关闭此窗口..."
  fi
}

medgate_fail() {
  echo
  echo "启动失败：$1"
  medgate_pause
  exit 1
}

medgate_cleanup() {
  if [[ -n "$medgate_server_pid" ]] && kill -0 "$medgate_server_pid" 2>/dev/null; then
    kill "$medgate_server_pid" 2>/dev/null
    wait "$medgate_server_pid" 2>/dev/null
  fi
}

trap medgate_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

echo "========================================"
echo " MedGate 本地启动器"
echo "========================================"

medgate_python="$(command -v python3 2>/dev/null)"
[[ -n "$medgate_python" ]] || medgate_fail "未找到 python3。请先安装 Python 3。"

medgate_health_response="$(/usr/bin/curl -fsS --max-time 1 "$medgate_health_url" 2>/dev/null || true)"
if [[ "$medgate_health_response" == *'"status":"ok"'* && "$medgate_health_response" == *'"service":"medgate-api"'* ]]; then
  echo "检测到 MedGate 已在运行，正在打开页面..."
  if [[ "${MEDGATE_LAUNCHER_SKIP_OPEN:-0}" != "1" ]]; then
    /usr/bin/open "$medgate_app_url"
  fi
  exit 0
fi

if /usr/sbin/lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  medgate_fail "8000 端口正被其他程序占用，请先关闭占用程序。"
fi

if ! "$medgate_python" -c 'import fastapi, httpx, uvicorn' >/dev/null 2>&1; then
  echo "检测到 MedGate 依赖尚未安装。"
  medgate_install_reply=""
  read -r "medgate_install_reply?是否现在安装？需要时会联网下载依赖。[y/N] "
  if [[ "$medgate_install_reply" != [yY] ]]; then
    medgate_fail "请在项目目录执行 python3 -m pip install -e . 后重试。"
  fi
  "$medgate_python" -m pip install -e . || medgate_fail "依赖安装失败。"
fi

export MEDGATE_HOST="127.0.0.1"
export MEDGATE_PORT="8000"

echo
echo "正在启动 MedGate..."
"$medgate_python" -m medgate.api &
medgate_server_pid=$!

integer medgate_start_attempt=0
while (( medgate_start_attempt < 60 )); do
  if ! kill -0 "$medgate_server_pid" 2>/dev/null; then
    wait "$medgate_server_pid" 2>/dev/null
    medgate_fail "服务进程已提前退出，请查看上方日志。"
  fi

  medgate_health_response="$(/usr/bin/curl -fsS --max-time 1 "$medgate_health_url" 2>/dev/null || true)"
  if [[ "$medgate_health_response" == *'"status":"ok"'* && "$medgate_health_response" == *'"service":"medgate-api"'* ]]; then
    echo
    echo "MedGate 已启动：$medgate_app_url"
    echo "请在页面左下角“设置”中配置 DeepSeek API Key。"
    echo "关闭此窗口或按 Control-C 即可停止服务。"
    if [[ "${MEDGATE_LAUNCHER_SKIP_OPEN:-0}" != "1" ]]; then
      /usr/bin/open "$medgate_app_url" || echo "浏览器未自动打开，请手动访问 $medgate_app_url"
    fi
    wait "$medgate_server_pid"
    exit $?
  fi

  sleep 0.25
  (( medgate_start_attempt += 1 ))
done

medgate_fail "15 秒内未通过健康检查，请查看上方日志。"
