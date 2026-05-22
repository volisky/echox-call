from __future__ import annotations

import unittest

from echox_call.features.audio_analysis.postcall.schemas import CreatePostcallJobRequest


def _base_payload() -> dict[str, object]:
    return {
        "jjdh": "JJD_20260408_0001",
        "audioUrl": "https://example.com/audio/test.wav",
        "bjsj": "2026-04-08T09:30:00+08:00",
        "JCJXTJSDWMC": "白银市公安局",
        "JJDWMC": "白银分局",
        "GXDWMC": "白银分局",
        "bjdh": "13800000000",
        "bjrmc": "张三",
        "bjrxbdm": 1,
        "lxdh": "13800000000",
        "jqdz": "某小区 1 栋 2 单元 301",
        "bjnr": "报警人称有人持续威胁自己，要求民警尽快到场",
        "jqlbdm": "纠纷",
        "jqlxdm": "治安纠纷",
        "jqxldm": "人员冲突",
        "jqzldm": "威胁恐吓",
        "jqdj": "高",
        "callbackUrl": "https://example.com/callback",
        "asrResult": [
            {"speaker": "接警员", "text": "你好，兰州市110"},
            {"speaker": "报警人", "text": "我要报警，我被打了"},
        ],
    }


class CreatePostcallJobRequestTest(unittest.TestCase):
    def test_accepts_extended_alarm_context_fields(self) -> None:
        payload = _base_payload() | {
            "alarmContent": "这里是接警员填写的报警内容",
            "alarmAddress": "这里是警情地址",
            "isHighIncidentAddress": True,
            "riskPerson": {
                "idcard": "321282************",
                "tags": ["暴力犯罪前科", "标签数据矛盾"],
                "report": "该人员曾因寻衅滋事罪获刑释放，存在暴力行为前科",
            },
        }

        request = CreatePostcallJobRequest.model_validate(payload)

        raw_payload = request.raw_payload_json()
        self.assertEqual(raw_payload["alarmContent"], "这里是接警员填写的报警内容")
        self.assertEqual(raw_payload["alarmAddress"], "这里是警情地址")
        self.assertTrue(raw_payload["isHighIncidentAddress"])
        self.assertEqual(
            raw_payload["riskPerson"],
            {
                "idcard": "321282************",
                "tags": ["暴力犯罪前科", "标签数据矛盾"],
                "report": "该人员曾因寻衅滋事罪获刑释放，存在暴力行为前科",
            },
        )


if __name__ == "__main__":
    unittest.main()
