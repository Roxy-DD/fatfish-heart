/**
 * cosmos-mem 管理界面（Client 半）
 *
 * 严格照官方模板（templates/decoration/client.js，已读取确认）：
 *
 *   window.__ModuleLoader__.load({
 *     id: '@local/my-decoration',
 *     factory(require) {                      ← 方法简写
 *       const React = require('react');
 *       const h = React.createElement;        ← 关键：用 createElement
 *       ...
 *       return {
 *         inject: ['slots'],
 *         apply(ctx) {
 *           ctx.slots.inject('slot', () => ctx.slots.register({
 *             name: 'slot', id: 'x', order: 5,
 *           }, Component));                   ← 不传 inject 属性
 *         },
 *       };
 *     },
 *   });
 *
 * 我之前的核心错误：用 react/jsx-runtime 的 jsx 当 h。
 * jsx(type, props) 只吃两个参数，children 必须塞进 props.children；
 * 我写 h('div', {...}, child1, child2) 传的可变参数全被丢弃，
 * 所以只剩纯文本、图标消失、页面空白。
 * React.createElement(type, props, ...children) 才支持可变参数。
 */
window.__ModuleLoader__.load({
  id: '@local/dsh-cosmos-mem',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const useState = React.useState;
    const useEffect = React.useEffect;
    const useCallback = React.useCallback;
    const useMemo = React.useMemo;

    // ============================================================
    // 样式（只引用 --dsw-alias-* 主题 token）
    // ============================================================
    const S = {
      page: {
        padding: '20px 24px',
        color: 'var(--dsw-alias-label-primary)',
        fontSize: 13,
        lineHeight: 1.6,
        boxSizing: 'border-box',
        height: '100%',
        minHeight: 0,
        overflow: 'auto',
      },
      header: {
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 16, gap: 12,
      },
      title: { fontSize: 15, fontWeight: 600 },
      stats: {
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(108px, 1fr))',
        gap: 10, marginBottom: 18,
      },
      statCard: {
        background: 'var(--dsw-alias-bg-layer-1)',
        border: '1px solid var(--dsw-alias-border-l1)',
        borderRadius: 8, padding: '10px 12px',
      },
      statLabel: { fontSize: 11, color: 'var(--dsw-alias-label-caption)', marginBottom: 4 },
      statValue: { fontSize: 18, fontWeight: 600, fontVariantNumeric: 'tabular-nums' },
      toolbar: { display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' },
      input: {
        flex: '1 1 200px', minWidth: 140, padding: '6px 10px', fontSize: 12,
        borderRadius: 6, border: '1px solid var(--dsw-alias-border-l2)',
        background: 'var(--dsw-alias-bg-layer-1)',
        color: 'var(--dsw-alias-label-primary)', outline: 'none',
      },
      btn: {
        padding: '6px 12px', fontSize: 12, borderRadius: 6,
        border: '1px solid var(--dsw-alias-border-l2)',
        background: 'var(--dsw-alias-bg-layer-1)',
        color: 'var(--dsw-alias-label-primary)', cursor: 'pointer', whiteSpace: 'nowrap',
      },
      btnPrimary: {
        padding: '6px 12px', fontSize: 12, borderRadius: 6,
        border: '1px solid var(--dsw-alias-brand-primary)',
        background: 'var(--dsw-alias-brand-primary)',
        color: 'var(--dsw-alias-label-primary-inverted)',
        cursor: 'pointer', whiteSpace: 'nowrap',
      },
      btnSmall: {
        padding: '4px 8px', fontSize: 11, borderRadius: 5,
        border: '1px solid var(--dsw-alias-border-l2)',
        background: 'transparent', color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer',
      },
      btnDanger: {
        padding: '4px 8px', fontSize: 11, borderRadius: 5,
        border: '1px solid var(--dsw-alias-state-error-primary)',
        background: 'transparent', color: 'var(--dsw-alias-state-error-primary)', cursor: 'pointer',
      },
      row: {
        display: 'flex', alignItems: 'stretch', gap: 0,
        borderBottom: '1px solid var(--dsw-alias-border-l1)',
        background: 'var(--dsw-alias-bg-layer-1)',
        position: 'relative',
      },
      // 情绪色条：正=暖，负=冷，强度=深浅，中性=灰
      emotionBar: {
        width: 4, flex: 'none', alignSelf: 'stretch',
      },
      rowBody: { flex: 1, minWidth: 0, padding: '12px 14px' },
      rowText: { marginBottom: 8, wordBreak: 'break-word', fontSize: 13 },
      rowMeta: {
        display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11,
        color: 'var(--dsw-alias-label-caption)', alignItems: 'center',
      },
      rowDerived: {
        display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 11,
        color: 'var(--dsw-alias-label-secondary)', marginTop: 6,
        alignItems: 'center',
      },
      // 双维度进度条
      meterRow: {
        display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8,
        alignItems: 'center',
      },
      meter: {
        display: 'flex', alignItems: 'center', gap: 6, fontSize: 11,
        color: 'var(--dsw-alias-label-caption)',
      },
      meterTrack: {
        width: 70, height: 5, borderRadius: 3,
        background: 'var(--dsw-alias-bg-layer-2)',
        overflow: 'hidden',
      },
      meterFill: { height: '100%', borderRadius: 3 },
      actions: {
        display: 'flex', flexDirection: 'column', gap: 6,
        padding: '12px 10px', borderLeft: '1px solid var(--dsw-alias-border-l1)',
        alignItems: 'center', justifyContent: 'flex-start',
      },
      list: { border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, overflow: 'hidden' },
      empty: {
        padding: 40, textAlign: 'center', border: '1px solid var(--dsw-alias-border-l1)',
        borderRadius: 8, color: 'var(--dsw-alias-label-caption)', fontSize: 12,
      },
      err: {
        padding: 12, borderRadius: 6, marginBottom: 12, fontSize: 12,
        background: 'var(--dsw-alias-state-error-secondary)',
        color: 'var(--dsw-alias-state-error-primary)',
      },
      hint: { fontSize: 11, color: 'var(--dsw-alias-label-caption)', marginTop: 8, lineHeight: 1.5 },
      tabbar: {
        display: 'flex', gap: 4, borderBottom: '1px solid var(--dsw-alias-border-l1)',
        marginBottom: 16,
      },
      tab: {
        padding: '8px 16px', fontSize: 13, cursor: 'pointer',
        border: 'none', background: 'transparent',
        color: 'var(--dsw-alias-label-secondary)',
        borderBottom: '2px solid transparent',
      },
      tabActive: {
        padding: '8px 16px', fontSize: 13, cursor: 'pointer',
        border: 'none', background: 'transparent',
        color: 'var(--dsw-alias-label-primary)',
        borderBottom: '2px solid var(--dsw-alias-brand-primary)',
        fontWeight: 600,
      },
      timeline: {
        position: 'relative', paddingLeft: 24,
        borderLeft: '2px solid var(--dsw-alias-border-l1)',
        marginLeft: 8,
      },
      timelineItem: {
        position: 'relative', marginBottom: 16, paddingLeft: 16,
      },
      timelineDot: {
        position: 'absolute', left: -33, top: 6,
        width: 10, height: 10, borderRadius: '50%',
        border: '2px solid var(--dsw-alias-bg-layer-1)',
      },
      formRow: {
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '10px 0', borderBottom: '1px solid var(--dsw-alias-border-l1)',
      },
      formLabel: {
        flex: '0 0 160px', fontSize: 12, color: 'var(--dsw-alias-label-secondary)',
      },
      formControl: { flex: 1, display: 'flex', alignItems: 'center', gap: 8 },
      sectionTitle: {
        fontSize: 13, fontWeight: 600, margin: '18px 0 8px',
        color: 'var(--dsw-alias-label-primary)',
      },
    };

    function badge(bg, fg) {
      return {
        padding: '1px 6px', borderRadius: 4, fontSize: 10,
        background: bg, color: fg, whiteSpace: 'nowrap',
      };
    }

    // ============================================================
    // API
    // ============================================================
    const BASE = '/cosmos-mem';
    const api = {
      async list(o) {
        const p = new URLSearchParams();
        if (o.query) p.set('q', o.query);
        if (o.top) p.set('top', String(o.top));
        const r = await fetch(BASE + '/list?' + p.toString());
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async invalidate(o) {
        const r = await fetch(BASE + '/invalidate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(o),
        });
        return r.json();
      },
      async revise(o) {
        const r = await fetch(BASE + '/revise', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(o),
        });
        return r.json();
      },
      async getConfig() {
        const r = await fetch(BASE + '/config');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async setConfig(o) {
        const r = await fetch(BASE + '/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(o),
        });
        return r.json();
      },
      async getInjectHistory() {
        const r = await fetch(BASE + '/inject-history');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async getSleepHistory() {
        const r = await fetch(BASE + '/sleep-history');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async getAudit(kind) {
        const p = kind ? ('?kind=' + encodeURIComponent(kind)) : '';
        const r = await fetch(BASE + '/audit' + p);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async getNotes(q, topic, tier) {
        const p = new URLSearchParams();
        if (q) p.set('q', q);
        if (topic) p.set('topic', topic);
        if (tier) p.set('tier', tier);
        const r = await fetch(BASE + '/note?' + p.toString());
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async postNote(o) {
        const r = await fetch(BASE + '/note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(o),
        });
        return r.json();
      },
      async getTodos(includeDone) {
        const r = await fetch(BASE + '/todo' + (includeDone ? '?include_done=1' : ''));
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      },
      async postTodo(o) {
        const r = await fetch(BASE + '/todo', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(o),
        });
        return r.json();
      },
    };

    // ============================================================
    // 侧栏图标
    // ============================================================
    function PanelIcon(props) {
      const size = (props && props.size) || 20;
      const active = !!(props && props.active);
      const color = active
        ? 'var(--dsw-alias-brand-primary)'
        : 'var(--dsw-alias-label-secondary)';

      return h('svg', {
        viewBox: '0 0 24 24',
        width: size, height: size,
        fill: 'none', stroke: color, strokeWidth: 1.8,
        strokeLinecap: 'round', strokeLinejoin: 'round',
        'aria-hidden': true,
        style: { display: 'block', flex: 'none' },
      },
        h('path', { d: 'M12 4.5c-3.6 0-6.5 2.4-6.5 5.4 0 1.8 1.1 3.3 2.8 4.2v3.4c0 .6.5 1 1.1 1h5.2' }),
        h('path', { d: 'M12 4.5c3.6 0 6.5 2.4 6.5 5.4 0 1.8-1.1 3.3-2.8 4.2v3.4c0 .6-.5 1-1.1 1h-5.2' }),
        h('circle', { cx: 12, cy: 10, r: 2.2 }),
        h('path', { d: 'M9.5 17.5h5' })
      );
    }

    // ============================================================
    // 记忆行（增强版：情绪色条 + 双维度 + 关系链 + scope）
    // ============================================================
    function emotionColor(valence, affect) {
      // 情绪色条：强度决定深浅，方向决定冷暖
      const strength = Math.min(1, Math.max(0, affect || 0));
      const alpha = 0.25 + strength * 0.6; // 0.25~0.85
      if ((valence || 0) > 0.15) {
        // 正向 → 暖色（橙）
        return 'rgba(255, 145, 60, ' + alpha + ')';
      } else if ((valence || 0) < -0.15) {
        // 负向 → 冷色（蓝）
        return 'rgba(80, 140, 255, ' + alpha + ')';
      }
      // 中性 → 灰
      return 'rgba(140, 145, 155, ' + (0.2 + strength * 0.3) + ')';
    }

    function MemoryRow(props) {
      const item = props.item;
      const [editing, setEditing] = useState(false);
      const [draft, setDraft] = useState(item.raw_text || '');

      const cost = typeof item.cost === 'number' ? item.cost : 0;
      const affect = typeof item.affect === 'number' ? item.affect : 0;
      const valence = typeof item.valence === 'number' ? item.valence : 0;
      const invalid = !!item.invalid_at;
      const rev = typeof item.revision === 'number' ? item.revision : 1;

      const tags = [];
      // 分层保护（docs/72）：L0 不可变 / L1 常驻准则 / L2 设计基石
      if (item.level === 'L0') tags.push(['L0 不可变', 'var(--dsw-alias-state-error-tertiary)', 'var(--dsw-alias-state-error-primary)']);
      else if (item.level === 'L1') tags.push(['L1 常驻', 'var(--dsw-alias-state-warn-secondary)', 'var(--dsw-alias-state-warn-primary)']);
      else if (item.level === 'L2') tags.push(['L2 基石', 'var(--dsw-alias-state-business-tertiary)', 'var(--dsw-alias-state-business-primary)']);
      if (cost >= 0.7) tags.push(['高代价', 'var(--dsw-alias-state-warn-secondary)', 'var(--dsw-alias-state-warn-primary)']);
      if (affect >= 0.4) tags.push(['强情绪', 'var(--dsw-alias-state-business-tertiary)', 'var(--dsw-alias-state-business-primary)']);
      if (invalid) tags.push(['已失效', 'var(--dsw-alias-state-idle-primary)', 'var(--dsw-alias-label-primary-inverted)']);
      if ((item.issues || []).length) tags.push(['有缺陷', 'var(--dsw-alias-state-error-secondary)', 'var(--dsw-alias-state-error-primary)']);
      if (rev > 1) tags.push(['修订' + rev, 'var(--dsw-alias-bg-layer-2)', 'var(--dsw-alias-label-secondary)']);

      const valenceLabel = valence > 0.15 ? '正向' : (valence < -0.15 ? '负向' : '中性');

      const scopeNames = { existence: '存在', identity: '身份', time_exact: '精确时间', time_range: '时间范围', value: '数值', location: '地点' };
      const scopeBadges = (item.scope || []).map((s) => scopeNames[s] || s);

      // 关系链：被谁取代 / 取代谁
      const superseded = item.superseded_by ? String(item.superseded_by).slice(0, 8) : null;

      const created = item.created_at
        ? new Date(item.created_at * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
        : '';

      return h('div', { style: S.row },
        // 情绪色条
        h('div', { style: Object.assign({}, S.emotionBar, { background: emotionColor(valence, affect) }) }),

        h('div', { style: S.rowBody },
          editing
            ? h('textarea', {
                value: draft,
                onChange: (e) => setDraft(e.target.value),
                style: Object.assign({}, S.input, {
                  width: '100%', minHeight: 60, resize: 'vertical',
                  fontFamily: 'inherit', lineHeight: 1.5,
                }),
              })
            : h('div', { style: S.rowText }, item.raw_text),

          // 双维度进度条：代价 + 情绪
          h('div', { style: S.meterRow },
            h('div', { style: S.meter },
              h('span', null, '代价'),
              h('div', { style: S.meterTrack },
                h('div', { style: Object.assign({}, S.meterFill, {
                  width: (cost * 100) + '%',
                  background: 'var(--dsw-alias-state-warn-primary)',
                }) })
              ),
              h('span', null, cost.toFixed(2))
            ),
            h('div', { style: S.meter },
              h('span', null, '情绪'),
              h('div', { style: S.meterTrack },
                h('div', { style: Object.assign({}, S.meterFill, {
                  width: (affect * 100) + '%',
                  background: valence < -0.15 ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-state-success-primary)',
                }) })
              ),
              h('span', null, affect.toFixed(2) + ' ' + valenceLabel)
            )
          ),

          // scope + 结构化字段
          h('div', { style: S.rowDerived },
            scopeBadges.length
              ? h('span', null, '能答: ' + scopeBadges.join(' / '))
              : null,
            (item.triggers || []).length
              ? h('span', null, '触发: ' + item.triggers.slice(0, 3).join('/'))
              : null,
            (item.person || []).length
              ? h('span', null, '人物: ' + item.person.slice(0, 3).join('/'))
              : null,
            (item.time || []).length
              ? h('span', null, '时间: ' + item.time.slice(0, 3).join('/'))
              : null,
            (item.location || []).length
              ? h('span', null, '地点: ' + item.location.slice(0, 3).join('/'))
              : null,
            (item.issues || []).length
              ? h('span', null, '缺陷: ' + item.issues.join('/'))
              : null
          ),

          // 元信息 + 关系链
          h('div', { style: S.rowMeta },
            h('span', { style: { fontFamily: 'monospace', opacity: 0.7 } },
              String(item.ep_id || '').slice(0, 8)),
            created ? h('span', null, created) : null,
            superseded
              ? h('span', { style: { color: 'var(--dsw-alias-label-secondary)' } },
                  '被 ' + superseded + ' 取代')
              : null,
            tags.map((t, i) => h('span', { key: 't' + i, style: badge(t[1], t[2]) }, t[0]))
          )
        ),

        h('div', { style: S.actions },
          !editing ? h('button', {
            style: S.btnSmall, disabled: props.busy,
            onClick: () => setEditing(true),
          }, '编辑') : h('button', {
            style: S.btnSmall, disabled: props.busy,
            onClick: async () => { await props.onRevise(item.ep_id, draft); setEditing(false); },
          }, '保存'),
          h('button', {
            style: S.btnDanger, disabled: props.busy,
            onClick: () => props.onDelete(item.ep_id),
          }, '失效')
        )
      );
    }

    // ============================================================
    // 时间线视图
    // ============================================================
    function TimelineView(props) {
      const items = props.items || [];
      // 按时间升序（旧→新），时间线从上到下是时间递增
      const sorted = items.slice().sort((a, b) => (a.created_at || 0) - (b.created_at || 0));

      // 按天分组
      const groups = [];
      let curDay = null;
      for (const it of sorted) {
        const day = it.created_at
          ? new Date(it.created_at * 1000).toLocaleDateString('zh-CN')
          : '未知时间';
        if (day !== curDay) {
          curDay = day;
          groups.push({ day, items: [] });
        }
        groups[groups.length - 1].items.push(it);
      }

      return h('div', null,
        groups.map((g) => h('div', { key: g.day },
          h('div', { style: { fontSize: 12, fontWeight: 600, margin: '14px 0 8px', color: 'var(--dsw-alias-label-secondary)' } }, g.day),
          h('div', { style: S.timeline },
            g.items.map((it) => {
              const invalid = !!it.invalid_at;
              const cost = typeof it.cost === 'number' ? it.cost : 0;
              const affect = typeof it.affect === 'number' ? it.affect : 0;
              const dotColor = invalid
                ? 'var(--dsw-alias-label-caption)'
                : (cost >= 0.7 ? 'var(--dsw-alias-state-warn-primary)' : (affect >= 0.4 ? 'var(--dsw-alias-state-business-primary)' : 'var(--dsw-alias-brand-primary)'));
              const time = it.created_at
                ? new Date(it.created_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
                : '';
              return h('div', { key: it.ep_id, style: S.timelineItem },
                h('div', { style: Object.assign({}, S.timelineDot, { background: dotColor }) }),
                h('div', { style: { fontSize: 12, opacity: invalid ? 0.5 : 1, textDecoration: invalid ? 'line-through' : 'none' } },
                  it.raw_text),
                h('div', { style: { fontSize: 11, color: 'var(--dsw-alias-label-caption)', marginTop: 3 } },
                  time + (cost >= 0.7 ? ' · 高代价' : '') + (affect >= 0.4 ? ' · 强情绪' : '') + (invalid ? ' · 已失效' : ''))
              );
            })
          )
        ))
      );
    }

    // ============================================================
    // 配置面板
    // ============================================================
    function ConfigPage(props) {
      const [cfg, setCfg] = useState(null);
      const [saving, setSaving] = useState(false);
      const [msg, setMsg] = useState('');

      const load = useCallback(async () => {
        try {
          const r = await api.getConfig();
          setCfg((r && r.config) || {});
        } catch (e) {
          setMsg('读取配置失败：' + String(e && e.message));
        }
      }, []);

      useEffect(() => { load(); }, [load]);

      const save = async (patch) => {
        setSaving(true);
        setMsg('');
        try {
          const r = await api.setConfig(patch);
          if (r.ok) {
            setCfg(Object.assign({}, cfg, patch));
            setMsg('已保存（运行时生效，重启后恢复 cordis.patch.yml 默认值）');
          } else {
            setMsg('保存失败：' + (r.error || ''));
          }
        } catch (e) {
          setMsg('保存失败：' + String(e && e.message));
        } finally {
          setSaving(false);
        }
      };

      const toggle = (key) => () => save({ [key]: !cfg[key] });

      function numField(key, label, min, max, step) {
        return h('div', { style: S.formRow },
          h('div', { style: S.formLabel }, label),
          h('div', { style: S.formControl },
            h('input', {
              type: 'number', min, max, step,
              value: cfg[key],
              style: Object.assign({}, S.input, { width: 90 }),
              onChange: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) save({ [key]: v }); },
            })
          )
        );
      }

      function boolField(key, label, desc) {
        return h('div', { style: S.formRow },
          h('div', { style: S.formLabel }, label),
          h('div', { style: S.formControl },
            h('input', {
              type: 'checkbox', checked: !!cfg[key],
              onChange: toggle(key),
            }),
            desc ? h('span', { style: { fontSize: 11, color: 'var(--dsw-alias-label-caption)' } }, desc) : null
          )
        );
      }

      if (!cfg) return h('div', { style: S.empty }, '加载配置中…');

      return h('div', { style: S.page },
        h('div', { style: S.header },
          h('div', { style: S.title }, '记忆配置'),
          h('button', { style: S.btn, disabled: saving, onClick: load }, '刷新')
        ),
        msg ? h('div', { style: S.hint }, msg) : null,

        h('div', { style: S.sectionTitle }, '自动记忆'),
        boolField('autoRemember', '自动记忆', '对话后自动复盘，判断哪些值得记'),
        numField('reviewWindow', '复盘窗口', 1, 50, 1),
        numField('minImportance', '记忆门槛', 0, 1, 0.05),
        numField('minCost', '代价门槛', 0, 1, 0.05),

        h('div', { style: S.sectionTitle }, '自动注入'),
        boolField('autoInject', '自动注入', '会话开始时自动注入相关记忆'),
        numField('autoInjectTopK', '注入条数', 1, 20, 1),
        numField('autoInjectMaxChars', '注入字符上限', 100, 5000, 100),

        h('div', { style: S.sectionTitle }, '会话预判'),
        boolField('prewarm', '会话预判', '新会话开始时，用上次记忆推测接下来可能说什么'),
        numField('prewarmSlots', '预判记忆条数', 1, 20, 1),

        h('div', { style: S.sectionTitle }, '模型'),
        h('div', { style: S.formRow },
          h('div', { style: S.formLabel }, '复盘模型'),
          h('div', { style: S.formControl },
            h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } },
              (cfg.reviewProvider || '-') + ' / ' + (cfg.reviewModel || '-')),
            h('span', { style: { fontSize: 11, color: 'var(--dsw-alias-label-caption)' } },
              '（在 cordis.patch.yml 中配置）')
          )
        ),

        h('div', { style: S.sectionTitle }, '调试'),
        boolField('debugLog', '调试日志', '写入 plugin-debug.jsonl 便于排查'),

        h('div', { style: S.hint },
          '这些是运行时配置，改动立即生效，但重启插件后会恢复 cordis.patch.yml 里的默认值。',
          h('br', null),
          '要永久生效，请直接编辑 dsh-plugin/cordis.patch.yml。'
        )
      );
    }

    // ============================================================
    // 注入历史视图
    // ============================================================
    function InjectHistoryView(props) {
      const [history, setHistory] = useState([]);
      const [loading, setLoading] = useState(true);
      const [err, setErr] = useState('');

      const load = useCallback(async () => {
        setLoading(true);
        setErr('');
        try {
          const r = await api.getInjectHistory();
          setHistory((r && r.history) || []);
        } catch (e) {
          setErr('读取注入历史失败：' + String(e && e.message));
        } finally {
          setLoading(false);
        }
      }, []);

      useEffect(() => { load(); }, [load]);

      const modeLabel = (m) => (m === 'slots' ? '固定席位兜底' : '语义检索');

      return h('div', null,
        h('div', { style: S.header },
          h('div', { style: S.title }, '注入历史'),
          h('button', { style: S.btn, disabled: loading, onClick: load }, '刷新')
        ),
        err ? h('div', { style: S.err }, err) : null,
        h('div', { style: S.hint, marginBottom: 12 },
          '这里是每次「开新对话」时自动注入的记忆记录。对话过程中不再自动注入，由模型自己调 mem_search 查询。',
          h('br', null),
          '记录保存在内存中，重启插件后清空。'
        ),
        loading
          ? h('div', { style: S.empty }, '加载中…')
          : history.length === 0
            ? h('div', { style: S.empty }, '还没有注入记录。开一个新对话就会产生第一条。')
            : h('div', { style: S.list },
                history.map((h, i) => {
                  const time = h.at ? new Date(h.at).toLocaleString('zh-CN') : '';
                  return h('div', { key: 'h' + i, style: { padding: '14px', borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)' } },
                    h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-caption)', marginBottom: 6 } },
                      time + ' · ' + modeLabel(h.mode) + (h.sid ? ' · ' + h.sid : '')),
                    h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', marginBottom: 8 } },
                      '触发消息: ' + (h.query || '(空)')),
                    h.prewarm
                      ? h('div', { style: { fontSize: 11, color: 'var(--dsw-alias-state-business-primary)', marginBottom: 8, background: 'var(--dsw-alias-state-business-tertiary)', padding: '6px 8px', borderRadius: 4 } },
                          '前瞻: ' + h.prewarm)
                      : null,
                    (h.items || []).length
                      ? h('div', null,
                          (h.items || []).map((it, j) => h('div', {
                            key: 'it' + j,
                            style: { fontSize: 12, padding: '4px 0', paddingLeft: 12, borderLeft: '2px solid var(--dsw-alias-border-l1)', marginBottom: 4 },
                          },
                            (j + 1) + '. ' + it.text))
                        )
                      : h('div', { style: { fontSize: 11, color: 'var(--dsw-alias-label-caption)' } }, '(无注入记忆)')
                  );
                })
              )
      );
    }

    // ============================================================
    // 睡眠历史视图
    // ============================================================
    function SleepHistoryView(props) {
      const [history, setHistory] = useState([]);
      const [loading, setLoading] = useState(true);
      const [err, setErr] = useState('');

      const load = useCallback(async () => {
        setLoading(true);
        setErr('');
        try {
          const r = await api.getSleepHistory();
          setHistory((r && r.history) || []);
        } catch (e) {
          setErr('读取睡眠历史失败：' + String(e && e.message));
        } finally {
          setLoading(false);
        }
      }, []);

      useEffect(() => { load(); }, [load]);

      return h('div', null,
        h('div', { style: S.header },
          h('div', { style: S.title }, '睡眠历史'),
          h('button', { style: S.btn, disabled: loading, onClick: load }, '刷新')
        ),
        err ? h('div', { style: S.err }, err) : null,
        h('div', { style: S.hint, marginBottom: 12 },
          '这里是模型在任务/项目告一段落时，主动触发的「睡眠整理」记录。',
          h('br', null),
          '整理=合并碎片，遗忘=标记过时（不删除），强化=加深重要记忆。'
        ),
        loading
          ? h('div', { style: S.empty }, '加载中…')
          : history.length === 0
            ? h('div', { style: S.empty }, '还没有睡眠记录。当模型觉得一个任务完结时，会主动整理。')
            : h('div', { style: S.list },
                history.map((h, i) => {
                  const time = h.at ? new Date(h.at * 1000).toLocaleString('zh-CN') : '';
                  const merged = (h.merged || []).length;
                  const invalidated = (h.invalidated || []).length;
                  const bumped = (h.bumped || []).length;
                  return h('div', { key: 's' + i, style: { padding: '14px', borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)' } },
                    h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-caption)', marginBottom: 8 } },
                      time + ' · 动作 ' + (h.actions || 0) + ' 项'),
                    h('div', { style: { display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11, marginBottom: 6 } },
                      merged ? h('span', { style: badge('var(--dsw-alias-state-business-tertiary)', 'var(--dsw-alias-state-business-primary)') }, '合并 ' + merged + ' 对') : null,
                      invalidated ? h('span', { style: badge('var(--dsw-alias-state-idle-primary)', 'var(--dsw-alias-label-primary-inverted)') }, '遗忘 ' + invalidated + ' 条') : null,
                      bumped ? h('span', { style: badge('var(--dsw-alias-state-warn-secondary)', 'var(--dsw-alias-state-warn-primary)') }, '强化 ' + bumped + ' 条') : null
                    ),
                    (h.merged || []).map((m, j) => h('div', {
                      key: 'm' + j,
                      style: { fontSize: 11, padding: '3px 0', paddingLeft: 12, color: 'var(--dsw-alias-label-secondary)' },
                    },
                      '合并: ' + String(m.keep || '').slice(0, 8) + ' ← ' + String(m.drop || '').slice(0, 8)))
                  );
                })
              )
      );
    }

    // ============================================================
    // 审计日志视图
    // ============================================================
    const KIND_NAMES = {
      inject: '注入', recall: '回忆', remember: '记忆',
      sleep: '睡眠', conflict: '矛盾', chain: '链式',
      affect: '情绪', config: '配置', error: '错误', warn: '警告',
    };

    function AuditView(props) {
      const [logs, setLogs] = useState([]);
      const [stats, setStats] = useState({});
      const [kind, setKind] = useState('');
      const [loading, setLoading] = useState(true);
      const [err, setErr] = useState('');

      const load = useCallback(async (k) => {
        setLoading(true);
        setErr('');
        try {
          const r = await api.getAudit(k || '');
          setLogs((r && r.logs) || []);
          setStats((r && r.stats) || {});
        } catch (e) {
          setErr('读取日志失败：' + String(e && e.message));
        } finally {
          setLoading(false);
        }
      }, []);

      useEffect(() => { load(''); }, [load]);

      const kinds = Object.keys(stats.by_kind || {});

      return h('div', null,
        h('div', { style: S.header },
          h('div', { style: S.title }, '审计日志'),
          h('button', { style: S.btn, disabled: loading, onClick: () => load(kind) }, '刷新')
        ),
        err ? h('div', { style: S.err }, err) : null,
        h('div', { style: S.hint, marginBottom: 12 },
          '记忆系统的全部动作日志，用于排查问题。按类型过滤，可看每次注入/回忆/记忆/睡眠/错误。'
        ),
        h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 } },
          h('button', {
            style: kind === '' ? S.btnPrimary : S.btn,
            onClick: () => { setKind(''); load(''); },
          }, '全部 (' + (stats.total || 0) + ')'),
          kinds.map((k) => h('button', {
            key: k,
            style: kind === k ? S.btnPrimary : S.btn,
            onClick: () => { setKind(k); load(k); },
          }, (KIND_NAMES[k] || k) + ' (' + stats.by_kind[k] + ')'))
        ),
        loading
          ? h('div', { style: S.empty }, '加载中…')
          : logs.length === 0
            ? h('div', { style: S.empty }, '还没有日志。')
            : h('div', { style: S.list },
                logs.map((l) => {
                  const time = l.at ? new Date(l.at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
                  const kindColor = l.kind === 'error' ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-secondary)';
                  return h('div', {
                    key: l.seq,
                    style: { padding: '8px 14px', borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', fontSize: 12 },
                  },
                    h('span', { style: { fontFamily: 'monospace', color: 'var(--dsw-alias-label-caption)', marginRight: 10 } }, time),
                    h('span', { style: { color: kindColor, fontWeight: 600, marginRight: 10 } }, KIND_NAMES[l.kind] || l.kind),
                    h('span', { style: { color: 'var(--dsw-alias-label-primary)' } }, l.msg || ''),
                    l.ep_id ? h('span', { style: { fontFamily: 'monospace', color: 'var(--dsw-alias-label-caption)', marginLeft: 8 } }, '[' + String(l.ep_id).slice(0, 8) + ']') : null
                  );
                })
              )
      );
    }

    // ============================================================
    // 笔记视图（三层知识：随笔 → 笔记 → 手册）
    // ============================================================
    const TIER_META = {
      scrap: { name: '随笔', icon: '✎', desc: '零散灵光、踩坑记录' },
      note: { name: '笔记', icon: '▤', desc: '按主题整理' },
      manual: { name: '手册', icon: '▣', desc: '体系化成书' },
    };

    function NoteView(props) {
      const [notes, setNotes] = useState([]);
      const [stats, setStats] = useState({});
      const [tier, setTier] = useState('');
      const [busy, setBusy] = useState(false);
      const [showNew, setShowNew] = useState(false);
      const [form, setForm] = useState({ title: '', body: '', topic: '', tier: 'scrap' });
      const [expanded, setExpanded] = useState({});
      const [err, setErr] = useState('');
      const [suggests, setSuggests] = useState([]);

      const load = useCallback(async (t) => {
        setErr('');
        try {
          const r = await api.getNotes('', '', t || '');
          setNotes((r && r.notes) || []);
          setStats((r && r.stats) || {});
        } catch (e) {
          setErr('读取知识库失败：' + (e && e.message ? e.message : String(e)));
        }
      }, []);

      const loadSuggest = useCallback(async () => {
        try {
          const out = [];
          for (const a of ['suggest', 'suggest_scrap', 'suggest_manual']) {
            const r = await api.postNote({ action: a });
            if (r && r.ok && (r.topics || []).length) {
              out.push({ stage: r.stage, topics: r.topics });
            }
          }
          setSuggests(out);
        } catch (e) { /* ignore */ }
      }, []);

      useEffect(() => { load(''); loadSuggest(); }, [load, loadSuggest]);

      const onCreate = async () => {
        if (!form.title.trim() || !form.body.trim()) {
          setErr('标题和正文都不能为空');
          return;
        }
        setBusy(true);
        setErr('');
        try {
          const r = await api.postNote({ action: 'add', ...form });
          if (r && r.ok) {
            setForm({ title: '', body: '', topic: '', tier: form.tier });
            setShowNew(false);
            await load(tier);
          } else {
            setErr('保存失败：' + ((r && r.error) || '未知'));
          }
        } finally {
          setBusy(false);
        }
      };

      const onPromote = async (id, to) => {
        setBusy(true);
        try {
          await api.postNote({ action: 'promote', note_id: id, to_tier: to });
          await load(tier);
        } finally {
          setBusy(false);
        }
      };

      const tierCounts = [['scrap', stats.scrap], ['note', stats.note], ['manual', stats.manual]];

      return h('div', null,
        h('div', { style: S.header },
          h('div', { style: S.title }, '知识库'),
          h('button', { style: S.btnPrimary, onClick: () => setShowNew(!showNew) },
            showNew ? '取消' : '写一条')
        ),
        err ? h('div', { style: S.err }, err) : null,
        h('div', { style: S.hint },
          '三层沉淀：随笔（随时记）→ 笔记（按主题整理）→ 手册（体系化成书）。' +
          '常用→记在记忆里；少见但重要→写成详细笔记；少见又不重要→简短随笔。'
        ),

        // 层级切换
        h('div', { style: { display: 'flex', gap: 8, margin: '12px 0', flexWrap: 'wrap' } },
          h('button', {
            style: tier === '' ? S.btnPrimary : S.btn,
            onClick: () => { setTier(''); load(''); },
          }, '全部 (' + (stats.valid || 0) + ')'),
          tierCounts.map(([t, c]) =>
            h('button', {
              key: t,
              style: tier === t ? S.btnPrimary : S.btn,
              onClick: () => { setTier(t); load(t); },
            }, TIER_META[t].icon + ' ' + TIER_META[t].name + ' (' + (c || 0) + ')')
          )
        ),

        // 演化建议
        suggests.length ? h('div', {
          style: {
            margin: '10px 0', padding: 12, borderRadius: 6,
            background: 'var(--dsw-alias-state-business-tertiary)',
            borderLeft: '3px solid var(--dsw-alias-state-business-primary)',
            fontSize: 12,
          },
        },
          h('div', { style: { fontWeight: 600, marginBottom: 6 } }, '💡 可以整理了'),
          suggests.map((s, i) =>
            h('div', { key: 'sg' + i, style: { marginBottom: 4 } },
              h('span', { style: { color: 'var(--dsw-alias-label-secondary)' } },
                '[' + s.stage + '] '),
              s.topics.slice(0, 3).map((t) => t.topic + '(' + t.count + ')').join('、')
            )
          )
        ) : null,

        // 新建表单
        showNew ? h('div', {
          style: {
            margin: '14px 0', padding: 14,
            border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 6,
          },
        },
          h('div', { style: { display: 'flex', gap: 8, marginBottom: 8 } },
            h('select', {
              style: Object.assign({}, S.input, { width: 110, flex: 'none' }),
              value: form.tier,
              onChange: (e) => setForm({ ...form, tier: e.target.value }),
            },
              h('option', { value: 'scrap' }, '✎ 随笔'),
              h('option', { value: 'note' }, '▤ 笔记'),
              h('option', { value: 'manual' }, '▣ 手册')
            ),
            h('input', {
              style: Object.assign({}, S.input, { flex: 1 }),
              placeholder: '标题（如：FTS5 JOIN 会回表）',
              value: form.title,
              onChange: (e) => setForm({ ...form, title: e.target.value }),
            })
          ),
          h('input', {
            style: Object.assign({}, S.input, { width: '100%', marginBottom: 8 }),
            placeholder: '主题（如：sqlite / python / 项目架构）',
            value: form.topic,
            onChange: (e) => setForm({ ...form, topic: e.target.value }),
          }),
          h('textarea', {
            style: Object.assign({}, S.input, {
              width: '100%', minHeight: 140, fontFamily: 'monospace', resize: 'vertical',
            }),
            placeholder: form.tier === 'scrap'
              ? '随手记下就行，不用成体系...'
              : (form.tier === 'manual' ? '体系化内容，支持 Markdown 章节...' : '按主题写清楚，能独立看懂...'),
            value: form.body,
            onChange: (e) => setForm({ ...form, body: e.target.value }),
          }),
          h('button', {
            style: Object.assign({}, S.btnPrimary, { marginTop: 8 }),
            disabled: busy, onClick: onCreate,
          }, busy ? '保存中…' : '保存')
        ) : null,

        // 列表
        notes.length === 0
          ? h('div', { style: S.empty },
              tier ? '这一层还没有内容。' : '知识库还是空的。踩到坑就随手记一条随笔。')
          : h('div', { style: S.list },
              notes.map((n) => {
                const isOpen = !!expanded[n.note_id];
                const meta = TIER_META[n.tier] || TIER_META.note;
                const date = n.updated_at
                  ? new Date(n.updated_at * 1000).toLocaleDateString('zh-CN')
                  : '';
                const canPromote = n.tier === 'scrap' || n.tier === 'note';
                return h('div', {
                  key: n.note_id,
                  style: {
                    padding: 14, borderBottom: '1px solid var(--dsw-alias-border-l1)',
                    background: 'var(--dsw-alias-bg-layer-1)',
                  },
                },
                  h('div', {
                    style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' },
                    onClick: () => setExpanded({ ...expanded, [n.note_id]: !isOpen }),
                  },
                    h('div', { style: { fontSize: 13, fontWeight: 600 } },
                      (isOpen ? '▾ ' : '▸ ') + meta.icon + ' ' + n.title),
                    h('div', { style: { fontSize: 11, color: 'var(--dsw-alias-label-caption)', whiteSpace: 'nowrap' } },
                      (n.topic ? n.topic + ' · ' : '') + n.chars + ' 字' +
                      (n.revision > 1 ? ' · rev' + n.revision : '') + ' · ' + date)
                  ),
                  isOpen ? h('div', null,
                    h('div', {
                      style: {
                        marginTop: 10, paddingLeft: 10,
                        borderLeft: '2px solid var(--dsw-alias-border-l1)',
                        fontSize: 12, whiteSpace: 'pre-wrap', lineHeight: 1.75,
                      },
                    }, n.body),
                    canPromote ? h('div', { style: { marginTop: 10, display: 'flex', gap: 8 } },
                      h('button', {
                        style: S.btnSmall, disabled: busy,
                        onClick: () => onPromote(n.note_id, n.tier === 'scrap' ? 'note' : 'manual'),
                      }, n.tier === 'scrap' ? '↑ 升为笔记' : '↑ 升为手册')
                    ) : null
                  ) : null
                );
              })
            )
      );
    }

    // ============================================================
    // 待办视图（模型自己的任务队列）
    // ============================================================
    function TodoView(props) {
      const [todos, setTodos] = useState([]);
      const [stats, setStats] = useState({});
      const [includeDone, setIncludeDone] = useState(false);
      const [busy, setBusy] = useState(false);
      const [newTitle, setNewTitle] = useState('');

      const load = useCallback(async (withDone) => {
        try {
          const r = await api.getTodos(withDone);
          setTodos((r && r.todos) || []);
          setStats((r && r.stats) || {});
        } catch (e) {
          console.error('读取待办失败', e);
        }
      }, []);

      useEffect(() => { load(false); }, [load]);

      const onAdd = async () => {
        if (!newTitle.trim()) return;
        setBusy(true);
        try {
          await api.postTodo({ action: 'add', title: newTitle.trim() });
          setNewTitle('');
          await load(includeDone);
        } finally {
          setBusy(false);
        }
      };

      const onDone = async (id) => {
        setBusy(true);
        try {
          await api.postTodo({ action: 'done', todo_id: id });
          await load(includeDone);
        } finally {
          setBusy(false);
        }
      };

      const kindLabel = { task: '任务', note_hint: '📝 待整理', review: '回顾' };

      return h('div', null,
        h('div', { style: S.header },
          h('div', { style: S.title }, '待办'),
          h('button', { style: S.btn, onClick: () => load(includeDone) }, '刷新')
        ),
        h('div', { style: S.hint },
          '这是模型自己的任务队列，可以自主控制。' +
          '「待整理」（📝）表示某主题记忆攒多了，该整理成笔记了。'
        ),
        h('div', { style: S.stats },
          [['待办', stats.pending], ['进行中', stats.doing],
           ['已完成', stats.done], ['总计', stats.total]].map((c, i) =>
            h('div', { key: 'st' + i, style: S.statCard },
              h('div', { style: S.statLabel }, c[0]),
              h('div', { style: S.statValue }, String(c[1] == null ? '-' : c[1]))
            ))
        ),
        h('div', { style: S.toolbar },
          h('input', {
            style: S.input,
            placeholder: '新增待办…',
            value: newTitle,
            onChange: (e) => setNewTitle(e.target.value),
            onKeyDown: (e) => { if (e.key === 'Enter') onAdd(); },
          }),
          h('button', { style: S.btnPrimary, disabled: busy, onClick: onAdd }, '添加'),
          h('label', {
            style: {
              fontSize: 12, display: 'flex', alignItems: 'center', gap: 6,
              color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer',
            },
          },
            h('input', {
              type: 'checkbox',
              checked: includeDone,
              onChange: (e) => { setIncludeDone(e.target.checked); load(e.target.checked); },
            }),
            '含已完成'
          )
        ),
        todos.length === 0
          ? h('div', { style: S.empty }, '没有待办。')
          : h('div', { style: S.list },
              todos.map((t) => {
                const done = t.status === 'done';
                return h('div', {
                  key: t.todo_id,
                  style: {
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--dsw-alias-border-l1)',
                    background: 'var(--dsw-alias-bg-layer-1)',
                    opacity: done ? 0.55 : 1,
                  },
                },
                  h('input', {
                    type: 'checkbox',
                    checked: done,
                    disabled: busy || done,
                    onChange: () => onDone(t.todo_id),
                  }),
                  h('div', { style: { flex: 1, fontSize: 12 } },
                    h('div', {
                      style: { textDecoration: done ? 'line-through' : 'none' },
                    }, t.title),
                    t.detail ? h('div', {
                      style: { fontSize: 11, color: 'var(--dsw-alias-label-caption)', marginTop: 2 },
                    }, t.detail) : null
                  ),
                  h('span', {
                    style: {
                      fontSize: 10, padding: '1px 6px', borderRadius: 3,
                      background: 'var(--dsw-alias-bg-layer-2)',
                      color: 'var(--dsw-alias-label-secondary)',
                    },
                  }, kindLabel[t.kind] || t.kind)
                );
              })
            )
      );
    }

    // ============================================================
    // 记忆页面
    // ============================================================
    function MemoryPage(props) {
      const [items, setItems] = useState([]);
      const [stats, setStats] = useState(null);
      const [loading, setLoading] = useState(true);
      const [err, setErr] = useState('');
      const [q, setQ] = useState('');
      const [busy, setBusy] = useState(false);
      const [showInvalid, setShowInvalid] = useState(false);
      const [sortBy, setSortBy] = useState('time');      // time | cost | affect
      const [filter, setFilter] = useState('all');        // all | high_cost | high_affect | negative | positive
      const [tab, setTab] = useState('list');             // list | timeline | config
      const [dateFrom, setDateFrom] = useState('');       // 起始日期 YYYY-MM-DD
      const [dateTo, setDateTo] = useState('');           // 结束日期 YYYY-MM-DD

      const load = useCallback(async (query, withInvalid) => {
        setLoading(true);
        setErr('');
        try {
          const r = await api.list({
            query: query || '',
            top: 200,
            includeInvalid: !!withInvalid,
          });
          setItems((r && r.items) || []);
          setStats((r && r.stats) || null);
        } catch (e) {
          setErr('读取记忆失败：' + (e && e.message ? e.message : String(e)));
        } finally {
          setLoading(false);
        }
      }, []);

      useEffect(() => { load('', false); }, [load]);

      const onDelete = async (id) => {
        if (!window.confirm('确定删除这条记忆？（不会物理删除，只标记失效）')) return;
        setBusy(true);
        try {
          await api.invalidate({ ep_id: id });
          await load(q, showInvalid);
        } catch (e) {
          setErr('删除失败：' + String(e && e.message));
        } finally {
          setBusy(false);
        }
      };

      const onRevise = async (id, text) => {
        setBusy(true);
        try {
          await api.revise({ ep_id: id, new_text: text });
          await load(q, showInvalid);
        } catch (e) {
          setErr('保存失败：' + String(e && e.message));
        } finally {
          setBusy(false);
        }
      };

      const cards = useMemo(() => {
        if (!stats) return [];
        return [
          ['总条数', stats.total],
          ['有效', stats.valid],
          ['已失效', stats.invalid],
          ['高代价', stats.high_cost],
          ['强情绪', stats.high_affect],
          ['有缺陷', stats.incomplete],
          ['库大小', (stats.size_mb != null ? stats.size_mb + ' MB' : '-')],
        ];
      }, [stats]);

      // 排序 + 过滤后的展示列表
      const displayItems = useMemo(() => {
        let arr = (items || []).slice();
        // 时间范围过滤（created_at 是秒级 epoch）
        if (dateFrom) {
          const fromTs = new Date(dateFrom + 'T00:00:00').getTime() / 1000;
          arr = arr.filter((it) => (it.created_at || 0) >= fromTs);
        }
        if (dateTo) {
          const toTs = new Date(dateTo + 'T23:59:59').getTime() / 1000;
          arr = arr.filter((it) => (it.created_at || 0) <= toTs);
        }
        // 分类过滤
        if (filter === 'high_cost') arr = arr.filter((it) => (it.cost || 0) >= 0.7);
        else if (filter === 'high_affect') arr = arr.filter((it) => (it.affect || 0) >= 0.4);
        else if (filter === 'negative') arr = arr.filter((it) => (it.valence || 0) < -0.15);
        else if (filter === 'positive') arr = arr.filter((it) => (it.valence || 0) > 0.15);
        // 排序
        if (sortBy === 'cost') arr.sort((a, b) => (b.cost || 0) - (a.cost || 0));
        else if (sortBy === 'affect') arr.sort((a, b) => (b.affect || 0) - (a.affect || 0));
        else arr.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
        return arr;
      }, [items, sortBy, filter, dateFrom, dateTo]);

      return h('div', { style: S.page },
        h('div', { style: S.header },
          h('div', { style: S.title }, '记忆库'),
          h('button', {
            style: S.btn, disabled: loading,
            onClick: () => load(q, showInvalid),
          }, loading ? '加载中…' : '刷新')
        ),
        err ? h('div', { style: S.err }, err) : null,

        // Tab 切换
        h('div', { style: S.tabbar },
          h('button', { style: tab === 'list' ? S.tabActive : S.tab, onClick: () => setTab('list') }, '记忆列表'),
          h('button', { style: tab === 'timeline' ? S.tabActive : S.tab, onClick: () => setTab('timeline') }, '时间线'),
          h('button', { style: tab === 'inject' ? S.tabActive : S.tab, onClick: () => setTab('inject') }, '注入历史'),
          h('button', { style: tab === 'sleep' ? S.tabActive : S.tab, onClick: () => setTab('sleep') }, '睡眠历史'),
          h('button', { style: tab === 'audit' ? S.tabActive : S.tab, onClick: () => setTab('audit') }, '审计日志'),
          h('button', { style: tab === 'note' ? S.tabActive : S.tab, onClick: () => setTab('note') }, '笔记'),
          h('button', { style: tab === 'todo' ? S.tabActive : S.tab, onClick: () => setTab('todo') }, '待办'),
          h('button', { style: tab === 'config' ? S.tabActive : S.tab, onClick: () => setTab('config') }, '配置')
        ),

        // 时间范围选择器（list 和 timeline 共用；config/inject 不显示）
        (tab === 'list' || tab === 'timeline')
          ? h('div', {
              style: {
                display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                marginBottom: 14, fontSize: 12,
                color: 'var(--dsw-alias-label-secondary)',
              },
            },
              h('span', null, '时间范围'),
              h('span', null, '从'),
              h('input', {
                type: 'date',
                value: dateFrom,
                max: dateTo || undefined,
                onChange: (e) => setDateFrom(e.target.value),
                style: Object.assign({}, S.input, { width: 140, flex: 'none' }),
              }),
              h('span', null, '到'),
              h('input', {
                type: 'date',
                value: dateTo,
                min: dateFrom || undefined,
                onChange: (e) => setDateTo(e.target.value),
                style: Object.assign({}, S.input, { width: 140, flex: 'none' }),
              }),
              (dateFrom || dateTo)
                ? h('button', {
                    style: S.btnSmall,
                    onClick: () => { setDateFrom(''); setDateTo(''); },
                  }, '清除时间')
                : null,
              (dateFrom || dateTo)
                ? h('span', { style: { color: 'var(--dsw-alias-label-caption)' } },
                    '（已过滤 ' + displayItems.length + ' 条）')
                : null
            )
          : null,

        renderTab()
      );

      // 说明：原来用一长串嵌套三元表达式决定渲染哪个 Tab，
      // 嵌套层级过深（7 层），缩进和理解都极易出错，曾导致渲染异常。
      // 现在改成独立函数 + if/else。
      // 关键：这两个函数必须定义在 MemoryPage 内部（闭包），
      // 才能访问 tab / displayItems / cards 等局部变量。
      // （曾经定义在外部，导致渲染时 ReferenceError，界面白屏）
      function renderTab() {
        if (tab === 'config') return h(ConfigPage, null)
        if (tab === 'inject') return h(InjectHistoryView, null)
        if (tab === 'sleep') return h(SleepHistoryView, null)
        if (tab === 'audit') return h(AuditView, null)
        if (tab === 'note') return h(NoteView, null)
        if (tab === 'todo') return h(TodoView, null)
        if (tab === 'timeline') return h(TimelineView, { items: displayItems })
        return renderList()
      }

      function renderList() {
        return h('div', null,
        h('div', { style: S.stats },
          cards.map((c, i) => h('div', { key: 'c' + i, style: S.statCard },
            h('div', { style: S.statLabel }, c[0]),
            h('div', { style: S.statValue }, String(c[1] == null ? '-' : c[1]))
          ))
        ),
        h('div', { style: S.toolbar },
          h('input', {
            style: S.input,
            placeholder: '搜索记忆内容…',
            value: q,
            onChange: (e) => setQ(e.target.value),
            onKeyDown: (e) => { if (e.key === 'Enter') load(q, showInvalid); },
          }),
          h('button', { style: S.btnPrimary, onClick: () => load(q, showInvalid) }, '搜索'),
          h('select', {
            style: Object.assign({}, S.btn, { padding: '6px 8px' }),
            value: sortBy,
            onChange: (e) => setSortBy(e.target.value),
          },
            h('option', { value: 'time' }, '按时间'),
            h('option', { value: 'cost' }, '按代价'),
            h('option', { value: 'affect' }, '按情绪')
          ),
          h('select', {
            style: Object.assign({}, S.btn, { padding: '6px 8px' }),
            value: filter,
            onChange: (e) => setFilter(e.target.value),
          },
            h('option', { value: 'all' }, '全部'),
            h('option', { value: 'high_cost' }, '高代价'),
            h('option', { value: 'high_affect' }, '强情绪'),
            h('option', { value: 'negative' }, '负向情绪'),
            h('option', { value: 'positive' }, '正向情绪')
          ),
          h('label', {
            style: {
              fontSize: 12, display: 'flex', alignItems: 'center', gap: 6,
              color: 'var(--dsw-alias-label-secondary)',
              whiteSpace: 'nowrap', cursor: 'pointer',
            },
          },
            h('input', {
              type: 'checkbox',
              checked: showInvalid,
              onChange: (e) => {
                const next = e.target.checked;
                setShowInvalid(next);
                load(q, next);
              },
            }),
            '包含已失效'
          )
        ),
        loading && items.length === 0
          ? h('div', { style: S.empty }, '加载中…')
          : displayItems.length === 0
            ? h('div', { style: S.empty }, items.length === 0
                ? '还没有记忆。跟助手聊几句，它会自己判断该记什么。'
                : '当前筛选条件下没有记忆。')
            : h('div', { style: S.list },
                displayItems.map((it) => h(MemoryRow, {
                  key: it.ep_id, item: it, busy: busy,
                  onDelete: onDelete, onRevise: onRevise,
                })
              )
            ),
        h('div', { style: S.hint },
          '记忆永不物理删除，删除只是标记失效，仍可查证历史。',
          h('br', null),
          '系统会自动判断该记什么，也可以直接对助手说「记住 …」。'
        )
        )
      }
    }

  // ============================================================
  // 导出（照官方模板：返回 { inject, apply }）
  // ============================================================
    return {
      inject: ['slots'],
      apply(ctx) {
        // 关闭面板：照 mcp-connector，通过 layout 服务切换
        const closePanel = () => {
          try {
            const layout = typeof ctx.get === 'function' ? ctx.get('layout') : null;
            if (layout && typeof layout.selectPanel === 'function') {
              layout.selectPanel(null);
            }
          } catch { /* 关闭失败不影响页面本身 */ }
        };

        // 1) 侧栏入口（id 必须与 main 的 key 一致）
        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: 'cosmos-mem',
          order: 20,
          label: () => '记忆',
        }, PanelIcon));

        // 2) 主面板
        ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: 'cosmos-mem',
          inject: () => ({ presentation: 'panel', closePanel }),
        }, MemoryPage));

        // 3) 设置页（备用入口）
        ctx.slots.inject('settings.section', () => ctx.slots.register({
          name: 'settings.section',
          id: 'cosmos-mem',
          order: 45,
          label: () => '记忆',
        }, MemoryPage));
      },
    };
  },
});

