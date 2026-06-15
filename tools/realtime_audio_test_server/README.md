# Realtime Audio Test Server

This is a standalone receiver for testing the ASR gateway realtime audio push contract.
It accepts the same multipart endpoint as the production gateway receiver, saves chunks
to disk, and exposes stream status APIs.

## Start

```bash
cd tools/realtime_audio_test_server
mkdir -p data
docker compose up -d
```

Default port:

```text
http://127.0.0.1:8022
```

## Endpoints

```text
GET  /health
POST /gateway/asr/push
GET  /streams
GET  /streams/{callId}
GET  /streams/{callId}/chunks/{seq}
```

`POST /gateway/asr/push` expects:

```text
file
vendor_specific_param=agentid=001650887;usrdn=19299487507;callid=test-call-001;
voice_id
seq
final
end
voice_format
```

When testing with curl, send `vendor_specific_param` with `--form-string`; otherwise
curl may treat semicolons as multipart attributes.

```bash
curl -X POST http://127.0.0.1:8022/gateway/asr/push \
  -H "Tracking-Id: test-001" \
  -F "file=@chunk0.wav;type=audio/wav" \
  --form-string "vendor_specific_param=agentid=001650887;usrdn=19299487507;callid=test-call-001;" \
  -F "voice_id=testvoice000001" \
  -F "seq=0" \
  -F "final=0" \
  -F "end=0" \
  -F "voice_format=12"
```
