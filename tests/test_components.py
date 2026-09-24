"""组件定义：它现在是界面的唯一事实源。

这份定义错了不会让测试红——界面会照着画出一个错的表单，而且看起来很正常。
所以这里检查的不是"能不能跑"，是**定义本身自洽吗、和真实代码对得上吗**。
"""

from __future__ import annotations

import pytest

from backtest.components import (
    COMPONENTS,
    COUPLINGS,
    LAYERS,
    as_form_spec,
    describe,
    gaps,
)


class TestDefinition:
    def test_every_component_says_what_it_decides(self):
        """role 是这份定义存在的理由——没有它，组件就只是个名字。"""
        for c in COMPONENTS:
            assert len(c.role) > 10, f"{c.name} 没写清在交易里决定什么"
            assert c.output, f"{c.name} 没写输出什么"

    def test_unfinished_components_say_what_is_missing(self):
        for c in COMPONENTS:
            if c.status != "done":
                assert c.gap, f"{c.name} 状态是 {c.status} 却没写缺什么"

    def test_discovered_components_say_how_to_find(self):
        """标成"研究出来的"却不说怎么研究，等于让人拍脑袋。"""
        for c in COMPONENTS:
            if c.origin == "discovered":
                assert len(c.how_to_find) > 50, f"{c.name} 没写怎么找"

    def test_every_component_sits_in_a_known_layer(self):
        known = {name for name, _ in LAYERS}
        for c in COMPONENTS:
            assert c.layer in known, f"{c.name} 的层 {c.layer!r} 不在 LAYERS 里"

    def test_couplings_reference_real_components(self):
        keys = {c.key for c in COMPONENTS}
        for a, b, why in COUPLINGS:
            assert a in keys and b in keys, f"联动关系引用了不存在的组件：{a} / {b}"
            assert len(why) > 10

    def test_keys_unique(self):
        keys = [c.key for c in COMPONENTS]
        assert len(keys) == len(set(keys))


class TestFields:
    def test_field_names_are_unique_across_components(self):
        """字段名会直接变成表单的 name，重名会让后端收到错的值。"""
        seen: dict[str, str] = {}
        for c in COMPONENTS:
            for f in c.inputs:
                assert f.name not in seen, f"{f.name} 同时属于 {seen[f.name]} 和 {c.name}"
                seen[f.name] = c.name

    def test_select_fields_have_options_and_valid_default(self):
        for c in COMPONENTS:
            for f in c.inputs:
                if f.kind == "select":
                    assert f.options, f"{f.name} 是 select 却没有选项"
                    values = [v for v, _ in f.options]
                    assert f.default in values, f"{f.name} 的默认值不在选项里"

    def test_depends_points_at_an_existing_field(self):
        """依赖写错会让那个字段**永远不显示**，而且不报错。"""
        names = {f.name for c in COMPONENTS for f in c.inputs}
        for c in COMPONENTS:
            for f in c.inputs:
                if f.depends:
                    src, _, want = f.depends.partition("=")
                    assert src in names, f"{f.name} 依赖了不存在的字段 {src}"
                    assert want, f"{f.name} 的 depends 没写取值"

    def test_depends_value_is_a_real_option(self):
        by_name = {f.name: f for c in COMPONENTS for f in c.inputs}
        for c in COMPONENTS:
            for f in c.inputs:
                if not f.depends:
                    continue
                src, _, want = f.depends.partition("=")
                opts = [v for v, _ in by_name[src].options]
                if opts:
                    assert want in opts, f"{f.name} 依赖 {src}={want}，但那不是可选值"

    def test_numeric_bounds_contain_the_default(self):
        for c in COMPONENTS:
            for f in c.inputs:
                if f.kind in ("number", "ratio", "int") and f.default is not None:
                    if f.lo is not None:
                        assert f.default >= f.lo, f"{f.name} 默认值低于下限"
                    if f.hi is not None:
                        assert f.default <= f.hi, f"{f.name} 默认值高于上限"


class TestFormSpec:
    def test_covers_every_component_exactly_once(self):
        spec = as_form_spec()
        listed = [c["key"] for L in spec for c in L["components"]]
        assert sorted(listed) == sorted(c.key for c in COMPONENTS)

    def test_layers_in_trading_order(self):
        """层的顺序就是交易里发生的顺序，不能乱。"""
        assert [L["layer"] for L in as_form_spec()] == [n for n, _ in LAYERS]

    def test_serialisable(self):
        """界面通过 JSON 拿这份定义——不能序列化就等于界面拿不到。"""
        import json

        json.dumps(as_form_spec(), ensure_ascii=False)

    def test_gaps_generated_not_handwritten(self):
        """缺口清单必须与组件状态一致。手写的清单会在功能补上后忘了改。"""
        assert len(gaps()) == sum(1 for c in COMPONENTS if c.status != "done" and c.gap)


class TestDescribe:
    def test_describe_lists_all(self):
        assert len(describe()) == len(COMPONENTS)

    def test_fields_match_real_run_parameters(self):
        """表单字段必须是 backtest.run 真认识的东西，否则填了没用还不报错。"""
        import inspect

        from backtest import run

        sig = set(inspect.signature(run).parameters)
        # 这些由前端组装后换个名字送进去，不直接对应 run 的参数
        indirect = {
            "entry_mode", "entry_code", "review_code", "strike_mode", "moneyness", "delta", "dte",
            "right", "take_profit", "stop_loss", "hold_days", "dte_floor",
            "sizing_method", "risk_pct", "notional", "target_vol", "kelly_fraction",
        }
        for c in COMPONENTS:
            for f in c.inputs:
                assert f.name in sig or f.name in indirect, (
                    f"{f.name} 既不是 run() 的参数，也没登记为间接字段——"
                    "界面上填了它不会有任何效果"
                )
