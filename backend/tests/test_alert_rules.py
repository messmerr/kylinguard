import sqlite3

from kylinguard.alert_rules import AlertRuleStore


def _create_legacy_database(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            metric TEXT NOT NULL,
            operator TEXT NOT NULL DEFAULT '>=',
            threshold REAL NOT NULL DEFAULT 0,
            severity TEXT NOT NULL DEFAULT 'warning',
            silence_minutes INTEGER NOT NULL DEFAULT 10,
            channel_ids TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE TABLE alert_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE TABLE alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER,
            rule_name TEXT NOT NULL,
            metric TEXT NOT NULL,
            metric_value TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            channels_notified TEXT NOT NULL DEFAULT '[]',
            fired_at REAL NOT NULL
        );
        INSERT INTO alert_channels(id,name,type,config,enabled,created_at)
            VALUES(4,'现存渠道','webhook','{}',1,1);
        INSERT INTO alert_rules(
            id,name,metric,operator,threshold,severity,silence_minutes,
            channel_ids,enabled,created_at
        ) VALUES(5,'旧规则','memory_pct','>=',80,'warning',10,'[4,5]',1,1);
        INSERT INTO alert_history(
            id,rule_id,rule_name,metric,metric_value,severity,message,
            channels_notified,fired_at
        ) VALUES(7,5,'旧规则','memory_pct','81%','warning','旧记录','[]',123);
    """)
    connection.commit()
    connection.close()


def test_旧库迁移不会把历史变成待处理且会清理失效渠道(tmp_path):
    database = tmp_path / "alerts.db"
    _create_legacy_database(database)

    store = AlertRuleStore(database)
    try:
        assert store.list_pending() == []
        history = store.list_history()
        assert history[0].acknowledged_at == history[0].fired_at == 123
        assert store.get_rule(5).channel_ids == [4]
    finally:
        store.close()

    # 迁移必须可重复执行。
    reopened = AlertRuleStore(database)
    try:
        assert reopened.list_pending() == []
        assert reopened.get_rule(5).channel_ids == [4]
    finally:
        reopened.close()


def test_规则待处理可跨重启确认且清历史保留未确认记录(tmp_path):
    database = tmp_path / "alerts.db"
    store = AlertRuleStore(database)
    history_id = store.record_trigger(
        rule_id=9,
        rule_name="CPU 测试",
        metric="cpu_pct",
        metric_value="12%",
        severity="critical",
        message="CPU 命中",
    )
    store.close()

    reopened = AlertRuleStore(database)
    try:
        pending = reopened.list_pending()
        assert [entry.id for entry in pending] == [history_id]
        reopened.clear_history()
        assert [entry.id for entry in reopened.list_pending()] == [history_id]
        assert reopened.acknowledge_history(history_id) is True
        assert reopened.list_pending() == []
        assert reopened.list_history()[0].acknowledged_at is not None
        reopened.clear_history()
        assert reopened.list_history() == []
    finally:
        reopened.close()


def test_渠道写入和删除都会过滤规则中的无效引用(tmp_path):
    store = AlertRuleStore(tmp_path / "alerts.db")
    try:
        channel_id = store.add_channel("值班渠道", "webhook", {"url": "https://example.invalid"})
        rule_id = store.add_rule(
            "内存", "memory_pct", ">=", 80, "warning", 10,
            [channel_id, 999, channel_id],
        )
        assert store.get_rule(rule_id).channel_ids == [channel_id]
        assert store.delete_channel(channel_id) is True
        assert store.get_rule(rule_id).channel_ids == []
    finally:
        store.close()


def test_批量确认只返回本次实际处理的待处理记录(tmp_path):
    store = AlertRuleStore(tmp_path / "alerts.db")
    try:
        first = store.record_trigger(1, "规则一", "memory_pct", "8%", "warning", "命中")
        second = store.record_trigger(2, "规则二", "cpu_pct", "3%", "critical", "命中")
        assert store.acknowledge_history(first) is True
        assert store.acknowledge_all_pending() == [second]
        assert store.acknowledge_all_pending() == []
        assert store.list_pending() == []
        assert len(store.list_history()) == 2
    finally:
        store.close()
