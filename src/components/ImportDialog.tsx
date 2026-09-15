import { useEffect, useRef } from 'react';
import { Button, Callout, Dialog, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';

/**
 * 导入照片（SD 卡 / U 盘 → 照片库）。
 *
 * ★★ 设计上的三条硬约束（都是踩过才知道的）：
 *  ① **先看后拷** —— 复制几百张要十几分钟，不能点一下就开跑。
 *     所以是两个按钮：先「只看不复制」（脚本的 --dry-run，只读不写），
 *     看清"要拷几个、拷到哪"之后再「开始导入」。
 *  ② **进度必须看得见** —— 导入是真写盘、真耗时；看不到进度的话用户会以为死机、
 *     去强杀程序 —— 那是最不该中断的一步。进度由主进程逐行推过来（`import-progress`）。
 *  ③ **源卡只读** —— 复制逻辑一行都不在这里重写，全交给现成的导入脚本；
 *     它的契约就是"只复制，绝不移动/删除源文件"，拷完还会按文件数+总字节校验。
 */
export function ImportDialog() {
  const open = useStore((s) => s.importOpen);
  const closeImport = useStore((s) => s.closeImport);
  const cards = useStore((s) => s.importCards);
  const script = useStore((s) => s.importScript);
  const form = useStore((s) => s.importForm);
  const setForm = useStore((s) => s.setImportForm);
  const plan = useStore((s) => s.importPlan);
  const log = useStore((s) => s.importLog);
  const running = useStore((s) => s.importRunning);
  const busy = useStore((s) => s.importBusy);
  const detect = useStore((s) => s.detectImport);
  const pickScript = useStore((s) => s.pickImportScript);
  const preview = useStore((s) => s.previewImport);
  const run = useStore((s) => s.runImport);
  const libRoot = useStore((s) => s.libRoot);
  const changeLibRoot = useStore((s) => s.changeLibRoot);

  const logRef = useRef<HTMLDivElement>(null);

  /* 进度是**主进程推过来的事件**（不是 invoke 的返回值）——
     一次导入几百行输出，等返回值才显示的话，界面全程像死机。 */
  useEffect(() => {
    if (!open) return;
    const off = API.onImportProgress((line) => {
      useStore.getState().pushImportLog(line);
    });
    return () => {
      /* ⚠ contextBridge 的返回值不一定是函数（mock / 异常时可能给 undefined）——
         不判一下就会在卸载时抛未捕获异常，而那种错只在控制台里，界面上看不出来。 */
      if (typeof off === 'function') off();
    };
  }, [open]);

  /* 日志跟到底（导入时最后一行永远是"最新的"） */
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log.length]);

  const pickSourceDir = async () => {
    const p = await API.pickDirectory();
    if (p) setForm({ src: p });
  };

  const pickLib = async () => {
    const p = await API.pickDirectory();
    if (p) await changeLibRoot(p);
  };

  const canPreview = !!form.src && !!form.topic && !!form.place && !!script && !running;
  const canRun = canPreview && !!plan && !plan.error;

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && closeImport()}>
      <Dialog.Content
        data-import-dialog
        style={{ maxWidth: 660, width: '94vw', maxHeight: '86vh', overflow: 'auto' }}
      >
        <Dialog.Title>导入照片</Dialog.Title>
        <Dialog.Description size="1" style={{ color: 'var(--text-dim)' }}>
          把卡里的照片「复制」进照片库并按「日期_主题_地点」建档。源卡只读，一个字节都不动。
        </Dialog.Description>

        {/* ---- 0. 导入脚本还没配：先让选一次（路径写进配置，以后不用再选） ---- */}
        {!script && (
          <Callout.Root color="orange" mt="3" size="1">
            <Callout.Text>
              还没配「导入脚本」—— 复制这活是交给一个现成脚本来做的（不是这个程序自己写盘）。
            </Callout.Text>
            <Flex mt="2">
              <Button size="1" variant="solid" onClick={pickScript}>
                选择导入脚本（import_photos.py）…
              </Button>
            </Flex>
          </Callout.Root>
        )}

        {/* ---- 1. 源卡 ---- */}
        <Flex direction="column" gap="2" mt="4">
          <Flex align="center" gap="2">
            <Text size="2" weight="bold">
              1. 源卡
            </Text>
            <Button size="1" variant="ghost" disabled={busy || running} onClick={detect}>
              重新扫描
            </Button>
            <Button size="1" variant="ghost" disabled={running} onClick={pickSourceDir}>
              手动选目录…
            </Button>
          </Flex>
          {cards.length === 0 ? (
            <Text size="1" style={{ color: 'var(--c-orange)' }}>
              没找到带 DCIM 的卡 —— 先把卡插好再点「重新扫描」，或手动选一个目录。
            </Text>
          ) : (
            <Flex direction="column" gap="1">
              {cards.map((c) => {
                const on = form.src === c.path;
                return (
                  <button
                    key={c.path}
                    data-card={c.path}
                    onClick={() => setForm({ src: c.path })}
                    style={{
                      border: '1px solid ' + (on ? 'var(--accent)' : 'var(--line)'),
                      background: on ? 'var(--bg-hover)' : 'transparent',
                      color: on ? 'var(--text)' : 'var(--text-dim)',
                      borderRadius: 'var(--r-sm)',
                      padding: '6px 10px',
                      textAlign: 'left',
                      cursor: 'pointer',
                      fontSize: 12,
                    }}
                  >
                    {c.path}
                    <span style={{ opacity: 0.6, marginLeft: 8 }}>{c.n} 个文件</span>
                  </button>
                );
              })}
            </Flex>
          )}
          {form.src && (
            <Text size="1" style={{ color: 'var(--text-faint)' }} data-import-src>
              将从这个目录读：{form.src}
            </Text>
          )}
        </Flex>

        {/* ---- 2. 命名（决定新主题叫什么） ---- */}
        <Flex direction="column" gap="2" mt="4">
          <Text size="2" weight="bold">
            2. 命名
          </Text>
          <Flex gap="2">
            <Field
              label="主题"
              placeholder="互勉约拍"
              value={form.topic}
              disabled={running}
              onChange={(v) => setForm({ topic: v })}
            />
            <Field
              label="地点"
              placeholder="深圳园岭新村"
              value={form.place}
              disabled={running}
              onChange={(v) => setForm({ place: v })}
            />
            <Field
              label="日期"
              placeholder="留空 = 从拍摄时间推断"
              value={form.date}
              disabled={running}
              onChange={(v) => setForm({ date: v })}
            />
          </Flex>
          <Text size="1" style={{ color: 'var(--text-faint)' }}>
            文件夹会叫「日期_主题_地点」；同名已存在时自动加序号，**不会**偷偷合进旧文件夹。
          </Text>
        </Flex>

        {/* ---- 3. 拷到哪 ---- */}
        <Flex direction="column" gap="2" mt="4">
          <Flex align="center" gap="2">
            <Text size="2" weight="bold">
              3. 拷到哪
            </Text>
            <Button size="1" variant="ghost" disabled={running} onClick={pickLib}>
              换图库…
            </Button>
          </Flex>
          <Text size="1" style={{ color: 'var(--text-dim)' }} data-import-lib>
            {libRoot || '（默认：剩余空间最大的那块非系统盘下的「照片库」）'}
          </Text>
          <Text size="1" style={{ color: 'var(--text-faint)' }}>
            默认就是当前照片库 —— 拷到别处的话，工作台扫不到，看着像"导入没成功"。
          </Text>
        </Flex>

        {/* ---- 4. 计划 / 结果 ---- */}
        {plan && (
          <Flex direction="column" gap="2" mt="4" data-import-plan>
            <Text size="2" weight="bold">
              {plan.error ? '出错了' : plan.dryRun ? '4. 计划（还没复制任何东西）' : '4. 导入结果'}
            </Text>
            {plan.error ? (
              <Callout.Root color="red" size="1">
                <Callout.Text>{plan.error}</Callout.Text>
              </Callout.Root>
            ) : (
              <Flex
                direction="column"
                gap="1"
                p="2"
                style={{ background: 'var(--bg)', borderRadius: 'var(--r-sm)', fontSize: 12 }}
              >
                {plan.files != null && (
                  <Line k="要拷" v={`${plan.files} 个文件，共 ${plan.total || '—'}`} />
                )}
                {plan.types && <Line k="类型" v={plan.types} />}
                {plan.dates && <Line k="日期" v={plan.dates} />}
                {plan.dest && <Line k="目标" v={plan.dest} />}
                {!plan.dryRun && plan.copied != null && (
                  <Line
                    k="结果"
                    v={`新增 ${plan.copied}，跳过(已存在) ${plan.skipped}，失败 ${plan.failed}`}
                  />
                )}
                {plan.renamed && <Line k="提示" v={plan.renamed} />}
                {plan.warning && <Line k="警告" v={plan.warning} />}
                {plan.elapsed && <Line k="耗时" v={`${plan.elapsed} 秒`} />}
                {!plan.dryRun && plan.verified && <Line k="校验" v="文件数与总字节一致" />}
              </Flex>
            )}
          </Flex>
        )}

        {/* ---- 5. 进度 ---- */}
        {(running || log.length > 0) && (
          <Flex direction="column" gap="1" mt="4">
            <Text size="2" weight="bold">
              {running ? '正在复制…' : '输出'}
            </Text>
            <div
              ref={logRef}
              data-import-log
              style={{
                maxHeight: 150,
                overflow: 'auto',
                background: 'var(--bg)',
                borderRadius: 'var(--r-sm)',
                padding: '8px 10px',
                fontSize: 11.5,
                lineHeight: 1.6,
                color: 'var(--text-dim)',
                fontFamily: 'ui-monospace, Consolas, monospace',
              }}
            >
              {log.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
            </div>
          </Flex>
        )}

        <Flex gap="3" mt="4" justify="end">
          <Button variant="soft" color="gray" disabled={running} onClick={closeImport}>
            取消
          </Button>
          <Button
            variant="soft"
            data-import-preview
            disabled={!canPreview || busy}
            onClick={() => preview()}
          >
            {busy ? '看一眼…' : '只看不复制'}
          </Button>
          <Button
            variant="solid"
            data-import-run
            disabled={!canRun || busy}
            onClick={() => run()}
          >
            开始导入
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

/** 一行「标签 : 值」 */
function Line({ k, v }: { k: string; v: string }) {
  return (
    <Flex gap="2">
      <Text size="1" style={{ color: 'var(--text-faint)', minWidth: 42 }}>
        {k}
      </Text>
      <Text size="1" style={{ color: 'var(--text)', wordBreak: 'break-all' }}>
        {v}
      </Text>
    </Flex>
  );
}

/** 小输入框（表单三格用同一套样式） */
function Field({
  label,
  placeholder,
  value,
  disabled,
  onChange,
}: {
  label: string;
  placeholder?: string;
  value: string;
  disabled?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <label style={{ flex: 1, minWidth: 0 }}>
      <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', marginBottom: 2 }}>
        {label}
      </Text>
      <input
        data-import-field={label}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: '100%',
          background: 'var(--bg)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-sm)',
          color: 'var(--text)',
          padding: '6px 8px',
          fontSize: 12.5,
          outline: 'none',
          fontFamily: 'inherit',
        }}
      />
    </label>
  );
}
