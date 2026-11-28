import datetime as dt

from doubao_checkin import campaign, config


def test_tasks_from_nested_envelope():
    payload = {
        "data": {
            "tasks": [
                {
                    "campaign_code": "select_work_fields",
                    "title": "选领域",
                    "task_status": 2,
                    "task_id": "123",
                    "reward_summaries": [{"reward_amount": 100}],
                },
                {
                    "campaign_code": "pc_task_generate_document_v2",
                    "title": "生成文档",
                    "task_status": 1,
                },
            ]
        }
    }
    tasks = campaign.tasks_from(payload)
    assert [t.code for t in tasks] == ["select_work_fields", "pc_task_generate_document_v2"]
    assert tasks[0].claimable is True
    assert tasks[0].task_id == "123"
    assert tasks[0].reward == 100
    assert tasks[1].claimable is False


def test_tasks_from_ignores_duplicates():
    payload = {
        "tasks": [
            {"campaign_code": "a", "title": "A"},
            {"campaign_code": "a", "title": "A"},
        ]
    }
    assert len(campaign.tasks_from(payload)) == 1


def test_record_time_in_cn_tz():
    # 2026-11-27 09:33 北京时间
    record = {"create_time_ms": 1795743197000}
    stamp = campaign.record_time(record)
    assert stamp is not None
    assert stamp.tzinfo is not None
    assert stamp.astimezone(config.CN_TZ).date() == dt.date(2026, 11, 27)


def test_record_time_tolerates_garbage():
    assert campaign.record_time({"create_time_ms": "oops"}) is None
    assert campaign.record_time({}) is None
