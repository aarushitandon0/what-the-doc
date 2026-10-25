#!/bin/sh
# Container entrypoint: start Ollama, wait for it, load the model into memory, then run
# the API in public mode. The API serves the interface at / on the same port.
set -e

ollama serve >/tmp/ollama.log 2>&1 &

i=0
until curl -sf http://127.0.0.1:11434/api/version >/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "ollama did not start; last log lines:" >&2
        tail -20 /tmp/ollama.log >&2
        exit 1
    fi
    sleep 1
done
echo "ollama is up"

if ! ollama show "$GEMMA_MODEL" >/dev/null 2>&1; then
    echo "model $GEMMA_MODEL missing from the image; pulling it"
    ollama pull "$GEMMA_MODEL"
fi

# Load the model now, in the background, so the first visitor does not wait for it.
# keep_alive -1 keeps it loaded. The API's health check reports the runtime meanwhile.
(curl -sf http://127.0.0.1:11434/api/generate \
     -d "{\"model\": \"$GEMMA_MODEL\", \"keep_alive\": -1}" >/dev/null \
     && echo "model loaded") &

exec python api.py --host 0.0.0.0 --port "${PORT:-10000}"
