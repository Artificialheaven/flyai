import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import type { TranslationKey } from '../../i18n/index.js'
import { useChatStore } from '../../services/chat/store.js'
import { useAuth } from '../../services/flyai/auth.js'
import {
  getMySettings,
  putMySettings,
  type MySettings,
} from '../../services/flyai/admin.js'
import {
  addCommitment,
  addJob,
  addPoll,
  addSelfMemory,
  cancelCommitment,
  clearMood,
  closeCommitment,
  deleteJob,
  deletePoll,
  deleteSelfMemory,
  getMood,
  getMoodTimeline,
  getProactiveState,
  listCommitments,
  listJobs,
  listPolls,
  listSelfMemory,
  selfMemoryTraces,
  setProactiveEnabled,
  supersedeSelfMemory,
  toggleJob,
  togglePoll,
  type Commitment,
  type Job,
  type Poll,
  type ProactiveState,
  type SelfMemoryRow,
  type SelfMemoryTrace,
} from '../../services/flyai/proactor.js'
import {
  decideLoopRequest,
  interruptLoop,
  listLoopRequests,
  listLoopSessions,
  startLoop,
  stopLoop,
  type LoopRequest,
  type LoopSession,
} from '../../services/flyai/loop.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { Select } from '../base/Select.js'
import { showToast } from '../base/toastState.js'
import {
  errorText,
  NumberField,
  PanelEmpty,
  PanelError,
  SwitchRow,
  TextField,
} from '../settings/admin/AdminCommon.js'
import styles from './SessionAssistant.module.css'

type Tab = 'proactive' | 'mood' | 'memory' | 'commitments' | 'jobs' | 'polls' | 'loop'

const tabs: { value: Tab; label: TranslationKey; adminOnly?: boolean }[] = [
  { value: 'proactive', label: 'assistTabProactive' },
  { value: 'mood', label: 'assistTabMood' },
  { value: 'memory', label: 'assistTabMemory' },
  { value: 'commitments', label: 'assistTabCommitments' },
  { value: 'jobs', label: 'assistTabJobs' },
  { value: 'polls', label: 'assistTabPolls' },
  { value: 'loop', label: 'assistTabLoop', adminOnly: true },
]

const weekdayKeys = [
  'assistWeekdayMon',
  'assistWeekdayTue',
  'assistWeekdayWed',
  'assistWeekdayThu',
  'assistWeekdayFri',
  'assistWeekdaySat',
  'assistWeekdaySun',
] as const

const fmtTime = (seconds: number | undefined, never: string): string => {
  if (!seconds) {
    return never
  }
  const date = new Date(seconds * 1000)
  return Number.isNaN(date.getTime()) ? String(seconds) : date.toLocaleString()
}

const splitTags = (value: string): string[] =>
  value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)

/** 会话助手：主动行为开关、心情、自我记忆、承诺、定时作业、轮询与长跑。 */
export const SessionAssistantDialog = ({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) => {
  const t = useTranslate()
  const session = useChatStore((state) => state.sessionCache[sessionId])
  const user = useAuth((state) => state.user)
  const isAdmin = user?.role === 'admin'

  const [tab, setTab] = useState<Tab>('proactive')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState<{ title: string; body: string; action: () => Promise<void> } | null>(null)

  const aiSeats = useMemo(
    () => (session?.members ?? []).filter((member) => !member.is_user && member.character_id),
    [session]
  )
  const memberOptions = useMemo(
    () =>
      aiSeats.map((member) => ({
        value: member.character_id,
        label: member.name || member.character_id,
      })),
    [aiSeats]
  )

  /** ---------- 主动行为 ---------- */

  const [proactive, setProactive] = useState<ProactiveState | null>(null)
  const [proactiveOn, setProactiveOn] = useState(false)
  const [proactiveLoaded, setProactiveLoaded] = useState(false)

  const loadProactive = useCallback(async () => {
    try {
      const data = await getProactiveState(sessionId)
      setProactive(data.state)
      setProactiveOn(Boolean(data.state?.enabled))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setProactiveLoaded(true)
    }
  }, [sessionId, t])

  useEffect(() => {
    void loadProactive()
  }, [loadProactive])

  const run = async (key: string, action: () => Promise<void>, done?: string) => {
    if (busy) {
      return
    }
    setBusy(key)
    setError('')
    try {
      await action()
      if (done) {
        showToast(done)
      }
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy('')
    }
  }

  const toggleProactive = (enabled: boolean) =>
    run('proactive', async () => {
      await setProactiveEnabled(sessionId, enabled)
      await loadProactive()
    }, enabled ? t('assistProactiveEnabledToast') : t('assistProactiveDisabledToast'))

  /** ---------- 心情 ---------- */

  const [charId, setCharId] = useState('')
  const [mood, setMood] = useState<{ mood: number; dims: Record<string, number>; announce: string } | null>(null)
  const [moodRows, setMoodRows] = useState<
    { id: number; delta: number; reason: string; confidence: number; source: string; created_at: number }[]
  >([])
  const [moodLoading, setMoodLoading] = useState(false)

  useEffect(() => {
    if (!charId && memberOptions[0]) {
      setCharId(memberOptions[0].value)
    }
  }, [charId, memberOptions])

  const loadMood = useCallback(async () => {
    if (!charId) {
      return
    }
    setMoodLoading(true)
    try {
      const [view, timeline] = await Promise.all([
        getMood(sessionId, charId),
        getMoodTimeline(sessionId, charId),
      ])
      setMood({ mood: view.mood, dims: view.dims ?? {}, announce: view.announce_text || view.announce })
      setMoodRows(timeline.items)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setMoodLoading(false)
    }
  }, [charId, sessionId, t])

  useEffect(() => {
    if (tab === 'mood') {
      void loadMood()
    }
  }, [tab, loadMood])

  const resetMood = () =>
    run('mood', async () => {
      await clearMood(sessionId, charId)
      await loadMood()
    }, t('assistMoodCleared'))

  /** ---------- 自我记忆 ---------- */

  const [memRows, setMemRows] = useState<SelfMemoryRow[]>([])
  const [memLoading, setMemLoading] = useState(false)
  const [memInclude, setMemInclude] = useState(false)
  // 个人开关默认关闭；需要完整设置面板数据用于整体回写
  const [memEnabled, setMemEnabled] = useState(false)
  const [memSettings, setMemSettings] = useState<MySettings | null>(null)
  const [memStatement, setMemStatement] = useState('')
  const [memTags, setMemTags] = useState('')
  const [memImportance, setMemImportance] = useState('60')
  const [traces, setTraces] = useState<SelfMemoryTrace[] | null>(null)
  const [supersedeTarget, setSupersedeTarget] = useState<SelfMemoryRow | null>(null)
  const [supersedeDraft, setSupersedeDraft] = useState('')

  const loadMemory = useCallback(async () => {
    setMemLoading(true)
    try {
      const [data, settings] = await Promise.all([
        listSelfMemory(memInclude),
        getMySettings(),
      ])
      setMemRows(data.items)
      setMemSettings(settings)
      setMemEnabled(Boolean(settings.self_memory_enabled))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setMemLoading(false)
    }
  }, [memInclude, t])

  const toggleMemory = (enabled: boolean) =>
    run('memToggle', async () => {
      if (!memSettings) {
        return
      }
      const payload = { ...memSettings, self_memory_enabled: enabled }
      delete payload.active_preset_id
      const saved = await putMySettings(payload)
      setMemSettings(saved)
      setMemEnabled(Boolean(saved.self_memory_enabled))
    }, enabled ? t('assistMemEnabledToast') : t('assistMemDisabledToast'))

  useEffect(() => {
    if (tab === 'memory') {
      setTraces(null)
      void loadMemory()
    }
  }, [tab, loadMemory])

  const addMemory = () =>
    run('memAdd', async () => {
      const statement = memStatement.trim()
      if (!statement) {
        return
      }
      const importance = Math.min(100, Math.max(0, Math.trunc(Number(memImportance) || 0)))
      await addSelfMemory(statement, splitTags(memTags), importance)
      setMemStatement('')
      setMemTags('')
      await loadMemory()
      showToast(t('assistMemAdded'))
    })

  const confirmSupersede = () =>
    run('memSupersede', async () => {
      if (!supersedeTarget) {
        return
      }
      const statement = supersedeDraft.trim()
      if (!statement) {
        return
      }
      await supersedeSelfMemory(supersedeTarget.id, statement)
      setSupersedeTarget(null)
      setSupersedeDraft('')
      await loadMemory()
      showToast(t('assistMemAdded'))
    })

  const removeMemory = (row: SelfMemoryRow) =>
    setConfirm({
      title: t('adminConfirmDeleteTitle'),
      body: row.statement,
      action: async () => {
        await deleteSelfMemory(row.id)
        await loadMemory()
        showToast(t('adminDeleted'))
      },
    })

  const loadTraces = () =>
    run('memTraces', async () => {
      const data = await selfMemoryTraces()
      setTraces(data.items)
    })

  /** ---------- 承诺 ---------- */

  const [commitRows, setCommitRows] = useState<Commitment[]>([])
  const [commitStatus, setCommitStatus] = useState<'open' | 'all'>('open')
  const [commitContent, setCommitContent] = useState('')
  const [commitDue, setCommitDue] = useState('')
  const [commitOwner, setCommitOwner] = useState('')

  const loadCommitments = useCallback(async () => {
    try {
      const data = await listCommitments({
        sessionId,
        status: commitStatus === 'open' ? 'open' : undefined,
      })
      setCommitRows(data.items)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [commitStatus, sessionId, t])

  useEffect(() => {
    if (tab === 'commitments') {
      void loadCommitments()
    }
  }, [tab, loadCommitments])

  const addCommit = () =>
    run('commitAdd', async () => {
      const content = commitContent.trim()
      const dueAt = commitDue.trim()
      if (!content || !dueAt) {
        return
      }
      await addCommitment({
        sessionId,
        content,
        dueAt,
        ownerName: commitOwner.trim() || undefined,
      })
      setCommitContent('')
      setCommitDue('')
      await loadCommitments()
      showToast(t('assistCommitAdded'))
    })

  const closeCommit = (row: Commitment) =>
    run(`commitClose:${row.id}`, async () => {
      await closeCommitment(row.id)
      await loadCommitments()
      showToast(t('adminSaved'))
    })

  const cancelCommit = (row: Commitment) =>
    run(`commitCancel:${row.id}`, async () => {
      await cancelCommitment(row.id)
      await loadCommitments()
      showToast(t('adminSaved'))
    })

  /** ---------- 定时作业 ---------- */

  const [jobRows, setJobRows] = useState<Job[]>([])
  const [jobMode, setJobMode] = useState<'once' | 'daily' | 'weekly'>('daily')
  const [jobTime, setJobTime] = useState('')
  const [jobWeekday, setJobWeekday] = useState('0')
  const [jobBrief, setJobBrief] = useState('')

  const loadJobs = useCallback(async () => {
    try {
      const data = await listJobs(sessionId)
      setJobRows(data.items)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [sessionId, t])

  useEffect(() => {
    if (tab === 'jobs') {
      void loadJobs()
    }
  }, [tab, loadJobs])

  const addSchedule = () =>
    run('jobAdd', async () => {
      const hhmm = jobTime.trim()
      const brief = jobBrief.trim()
      if (!/^\d{1,2}:\d{2}$/.test(hhmm) || !brief) {
        return
      }
      await addJob({
        sessionId,
        mode: jobMode,
        hhmm,
        brief,
        weekday: jobMode === 'weekly' ? Number(jobWeekday) : undefined,
      })
      setJobBrief('')
      await loadJobs()
      showToast(t('assistJobAdded'))
    })

  const flipJob = (row: Job) =>
    run(`jobToggle:${row.id}`, async () => {
      await toggleJob(row.id, !row.enabled)
      await loadJobs()
    })

  const removeJob = (row: Job) =>
    setConfirm({
      title: t('adminConfirmDeleteTitle'),
      body: row.brief,
      action: async () => {
        await deleteJob(row.id)
        await loadJobs()
        showToast(t('adminDeleted'))
      },
    })

  /** ---------- 轮询 ---------- */

  const [pollRows, setPollRows] = useState<Poll[]>([])
  const [pollTitle, setPollTitle] = useState('')
  const [pollProbability, setPollProbability] = useState('10')
  const [pollEveryN, setPollEveryN] = useState('20')
  const [pollActiveHours, setPollActiveHours] = useState('12')

  const loadPolls = useCallback(async () => {
    try {
      const data = await listPolls(sessionId)
      setPollRows(data.items)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [sessionId, t])

  useEffect(() => {
    if (tab === 'polls') {
      void loadPolls()
    }
  }, [tab, loadPolls])

  const addTick = () =>
    run('pollAdd', async () => {
      const title = pollTitle.trim()
      if (!title) {
        return
      }
      await addPoll({
        sessionId,
        title,
        probability: Math.min(100, Math.max(1, Math.trunc(Number(pollProbability) || 10))),
        wakeEveryN: Math.max(1, Math.trunc(Number(pollEveryN) || 20)),
        activeHours: Math.max(1, Number(pollActiveHours) || 12),
      })
      setPollTitle('')
      await loadPolls()
      showToast(t('assistPollAdded'))
    })

  const flipPoll = (row: Poll) =>
    run(`pollToggle:${row.id}`, async () => {
      await togglePoll(row.id, !row.enabled)
      await loadPolls()
    })

  const removePoll = (row: Poll) =>
    setConfirm({
      title: t('adminConfirmDeleteTitle'),
      body: row.title,
      action: async () => {
        await deletePoll(row.id)
        await loadPolls()
        showToast(t('adminDeleted'))
      },
    })

  /** ---------- 长跑 ---------- */

  const [loopRuns, setLoopRuns] = useState<LoopSession[]>([])
  const [loopRequests, setLoopRequests] = useState<LoopRequest[]>([])
  const [loopTask, setLoopTask] = useState('')
  const [loopInterrupt, setLoopInterrupt] = useState('')

  const loadLoop = useCallback(async () => {
    try {
      const [sessions, requests] = await Promise.all([
        listLoopSessions(),
        listLoopRequests('pending'),
      ])
      setLoopRuns(sessions.items.filter((item) => item.session_id === sessionId))
      setLoopRequests(requests.items.filter((item) => item.session_id === sessionId))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [sessionId, t])

  useEffect(() => {
    if (tab === 'loop' && isAdmin) {
      void loadLoop()
    }
  }, [tab, isAdmin, loadLoop])

  const startRun = () =>
    run('loopStart', async () => {
      const task = loopTask.trim()
      if (!task) {
        return
      }
      const result = await startLoop({ sessionId, task })
      setLoopTask('')
      await loadLoop()
      showToast(result.run_id ? t('assistLoopStarted') : t('assistLoopStartedPending'))
    })

  const decideRequest = (row: LoopRequest, approve: boolean) =>
    run(`loopDecide:${row.id}`, async () => {
      if (!user) {
        return
      }
      await decideLoopRequest(row.id, user.id, approve)
      await loadLoop()
      showToast(t('adminSaved'))
    })

  const stopRun = (row: LoopSession) =>
    run(`loopStop:${row.id}`, async () => {
      await stopLoop(row.id)
      await loadLoop()
      showToast(t('adminSaved'))
    })

  const sendInterrupt = () =>
    run('loopInterrupt', async () => {
      const text = loopInterrupt.trim()
      if (!text) {
        return
      }
      await interruptLoop(sessionId, text)
      setLoopInterrupt('')
      showToast(t('assistLoopInterrupted'))
    })

  const visibleTabs = tabs.filter((item) => !item.adminOnly || isAdmin)

  return (
    <Modal
      title={t('sessionAssistant')}
      closable
      className={styles.dialog}
      mainClassName={styles.content}
      onClose={onClose}
      footer={
        <div className={styles.footer}>
          <Button variant="outlined" size="m" onClick={onClose}>
            {t('close')}
          </Button>
        </div>
      }
    >
      <div className={styles.tabs} role="tablist">
        {visibleTabs.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={tab === item.value}
            className={`${styles.tab} ${tab === item.value ? styles.tabActive : ''}`}
            onClick={() => {
              setError('')
              setTab(item.value)
            }}
          >
            {t(item.label)}
          </button>
        ))}
      </div>

      {error ? <PanelError message={error} /> : null}

      {tab === 'proactive' ? (
        <>
          <div className={styles.hint}>{t('assistProactiveHint')}</div>
          {proactiveLoaded ? (
            <>
              <SwitchRow
                label={t('assistProactiveEnabled')}
                desc={t('assistProactiveEnabledDesc')}
                checked={proactiveOn}
                onChange={(checked) => void toggleProactive(checked)}
              />
              <div className={styles.list}>
                <div className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemMeta}>
                      {t('assistLastWake')}: {fmtTime(proactive?.last_wake_at, t('assistNever'))}
                    </div>
                    <div className={styles.itemMeta}>
                      {t('assistLastSpeakup')}: {fmtTime(proactive?.last_speakup_at, t('assistNever'))}
                    </div>
                    <div className={styles.itemMeta}>
                      {t('assistLastHuman')}: {fmtTime(proactive?.last_human_at, t('assistNever'))}
                    </div>
                  </div>
                  <span className={styles.badge}>
                    {t('assistWakeMiss')} {proactive?.wake_miss_streak ?? 0}
                  </span>
                </div>
              </div>
            </>
          ) : (
            <PanelEmpty text={t('adminLoading')} />
          )}
        </>
      ) : null}

      {tab === 'mood' ? (
        <>
          <div className={styles.hint}>{t('assistMoodHint')}</div>
          {memberOptions.length === 0 ? (
            <PanelEmpty text={t('assistNoAiSeat')} />
          ) : (
            <>
              <div className={styles.filters}>
                <Select
                  value={charId}
                  ariaLabel={t('assistMoodMember')}
                  options={memberOptions}
                  onChange={setCharId}
                />
                <Button variant="outlined" size="s" disabled={moodLoading} onClick={() => void loadMood()}>
                  {t('adminRefresh')}
                </Button>
                <Button
                  variant="dangerOutlined"
                  size="s"
                  disabled={moodLoading || busy === 'mood'}
                  onClick={() => void resetMood()}
                >
                  {t('assistMoodClear')}
                </Button>
              </div>
              {moodLoading ? (
                <PanelEmpty text={t('adminLoading')} />
              ) : mood ? (
                <>
                  <div className={styles.moodBox}>
                    <div className={styles.moodValue}>{Math.round(mood.mood)}</div>
                    <div className={styles.moodText}>
                      {mood.announce ? <div className={styles.itemName}>{mood.announce}</div> : null}
                      <div className={styles.chips}>
                        {Object.entries(mood.dims).map(([key, value]) => (
                          <span key={key} className={styles.chip}>
                            {key} {Math.round(value)}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                  {moodRows.length === 0 ? (
                    <PanelEmpty text={t('memoryEmpty')} />
                  ) : (
                    <div className={styles.list}>
                      {moodRows.map((row) => (
                        <div key={row.id} className={styles.item}>
                          <div className={styles.itemMain}>
                            <div className={styles.itemName}>{row.reason || '—'}</div>
                            <div className={styles.itemMeta}>
                              {fmtTime(row.created_at, '')} · {row.source}
                              {row.confidence ? ` · ${row.confidence}%` : ''}
                            </div>
                          </div>
                          <span className={styles.badge}>
                            {row.delta > 0 ? '+' : ''}
                            {row.delta}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <PanelEmpty text={t('memoryEmpty')} />
              )}
            </>
          )}
        </>
      ) : null}

      {tab === 'memory' ? (
        <>
          <SwitchRow
            label={t('assistMemSwitch')}
            desc={t('assistMemSwitchDesc')}
            checked={memEnabled}
            onChange={(checked) => void toggleMemory(checked)}
          />
          <div className={styles.hint}>
            {memEnabled ? t('assistMemHint') : t('assistMemDisabledHint')}
          </div>
          <div className={styles.formCard}>
            <TextField
              label={t('assistMemStatement')}
              value={memStatement}
              onChange={setMemStatement}
            />
            <div className={styles.inlineFields}>
              <TextField
                label={t('assistMemTags')}
                hint={t('assistMemTagsHint')}
                value={memTags}
                onChange={setMemTags}
              />
              <NumberField
                label={t('assistMemImportance')}
                value={memImportance}
                onChange={setMemImportance}
              />
            </div>
            <div className={styles.actions}>
              <Button
                size="s"
                disabled={!memEnabled || busy === 'memAdd' || !memStatement.trim()}
                onClick={() => void addMemory()}
              >
                {t('assistMemAdd')}
              </Button>
            </div>
          </div>
          <div className={styles.filters}>
            <label className={styles.checkField}>
              <input
                type="checkbox"
                checked={memInclude}
                onChange={(event) => setMemInclude(event.target.checked)}
              />
              {t('memoryIncludeSuperseded')}
            </label>
            <span className={styles.spacer} />
            <Button variant="outlined" size="s" disabled={busy === 'memTraces'} onClick={() => void loadTraces()}>
              {t('assistMemTraces')}
            </Button>
          </div>
          {traces ? (
            <div className={styles.list}>
              {traces.length === 0 ? (
                <PanelEmpty text={t('memoryEmpty')} />
              ) : (
                traces.map((row) => (
                  <div key={row.id} className={styles.item}>
                    <div className={styles.itemMain}>
                      <div className={styles.itemMeta}>
                        {row.kind} · #{row.memory_id} · {fmtTime(row.created_at, '')}
                      </div>
                      <div className={styles.itemName}>{row.detail}</div>
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : null}
          {memLoading ? (
            <PanelEmpty text={t('adminLoading')} />
          ) : memRows.length === 0 ? (
            <PanelEmpty text={t('memoryEmpty')} />
          ) : (
            <div className={styles.list}>
              {memRows.map((row) => (
                <div key={row.id} className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemName}>
                      {row.statement}
                      {row.superseded ? <span className={styles.badge}>{t('memoryStatSuperseded')}</span> : null}
                    </div>
                    <div className={styles.chips}>
                      {splitTags(row.tags).map((tag) => (
                        <span key={tag} className={styles.chip}>
                          #{tag}
                        </span>
                      ))}
                      <span className={styles.chip}>{t('memoryImportance', { value: row.importance })}</span>
                      <span className={styles.chip}>{t('memoryAccess', { count: row.access_count })}</span>
                    </div>
                  </div>
                  <div className={styles.itemActions}>
                    {!row.superseded ? (
                      <Button
                        variant="outlined"
                        size="s"
                        disabled={!memEnabled}
                        onClick={() => {
                          setSupersedeTarget(row)
                          setSupersedeDraft('')
                        }}
                      >
                        {t('assistMemSupersede')}
                      </Button>
                    ) : null}
                    <Button variant="dangerOutlined" size="s" onClick={() => removeMemory(row)}>
                      {t('adminDelete')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {tab === 'commitments' ? (
        <>
          <div className={styles.hint}>{t('assistCommitHint')}</div>
          <div className={styles.formCard}>
            <TextField label={t('assistCommitContent')} value={commitContent} onChange={setCommitContent} />
            <div className={styles.inlineFields}>
              <TextField
                label={t('assistCommitDue')}
                hint={t('assistCommitDueHint')}
                value={commitDue}
                placeholder="21:30"
                onChange={setCommitDue}
              />
              <TextField label={t('assistCommitOwner')} value={commitOwner} onChange={setCommitOwner} />
            </div>
            <div className={styles.actions}>
              <Button size="s" disabled={busy === 'commitAdd'} onClick={() => void addCommit()}>
                {t('assistCommitAdd')}
              </Button>
            </div>
          </div>
          <div className={styles.filters}>
            <Select<'open' | 'all'>
              value={commitStatus}
              ariaLabel={t('assistCommitStatus')}
              options={[
                { value: 'open', label: t('assistCommitStatusOpen') },
                { value: 'all', label: t('assistCommitStatusAll') },
              ]}
              onChange={setCommitStatus}
            />
          </div>
          {commitRows.length === 0 ? (
            <PanelEmpty text={t('assistCommitEmpty')} />
          ) : (
            <div className={styles.list}>
              {commitRows.map((row) => (
                <div key={row.id} className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemName}>
                      {row.content}
                      <span className={styles.badge}>{row.status}</span>
                    </div>
                    <div className={styles.itemMeta}>
                      {row.owner_name ? `${row.owner_name} · ` : ''}
                      {t('assistCommitDueAt')} {fmtTime(row.due_at, '—')}
                    </div>
                  </div>
                  {row.status === 'open' ? (
                    <div className={styles.itemActions}>
                      <Button variant="outlined" size="s" onClick={() => void closeCommit(row)}>
                        {t('assistCommitClose')}
                      </Button>
                      <Button variant="dangerOutlined" size="s" onClick={() => void cancelCommit(row)}>
                        {t('assistCommitCancel')}
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {tab === 'jobs' ? (
        <>
          <div className={styles.hint}>{t('assistJobHint')}</div>
          <div className={styles.formCard}>
            <TextField label={t('assistJobBrief')} value={jobBrief} onChange={setJobBrief} />
            <div className={styles.inlineFields}>
              <Select<'once' | 'daily' | 'weekly'>
                value={jobMode}
                ariaLabel={t('assistJobMode')}
                options={[
                  { value: 'once', label: t('assistJobOnce') },
                  { value: 'daily', label: t('assistJobDaily') },
                  { value: 'weekly', label: t('assistJobWeekly') },
                ]}
                onChange={setJobMode}
              />
              <TextField
                label={t('assistJobTime')}
                hint="HH:MM"
                value={jobTime}
                placeholder="08:30"
                onChange={setJobTime}
              />
            </div>
            {jobMode === 'weekly' ? (
              <Select
                value={jobWeekday}
                ariaLabel={t('assistJobWeekday')}
                options={weekdayKeys.map((key, index) => ({ value: String(index), label: t(key) }))}
                onChange={setJobWeekday}
              />
            ) : null}
            <div className={styles.actions}>
              <Button size="s" disabled={busy === 'jobAdd'} onClick={() => void addSchedule()}>
                {t('assistJobAdd')}
              </Button>
            </div>
          </div>
          {jobRows.length === 0 ? (
            <PanelEmpty text={t('assistJobEmpty')} />
          ) : (
            <div className={styles.list}>
              {jobRows.map((row) => (
                <div key={row.id} className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemName}>
                      {row.brief}
                      <span className={styles.badge}>{row.hhmm}</span>
                      <span className={styles.badge}>
                        {row.mode === 'weekly'
                          ? t(weekdayKeys[row.weekday] ?? 'assistWeekdayMon')
                          : row.mode}
                      </span>
                    </div>
                    <div className={styles.itemMeta}>
                      {t('assistJobNext')}: {row.next_run_local || fmtTime(row.next_run_at, '—')}
                      {row.last_status ? ` · ${row.last_status}` : ''}
                    </div>
                  </div>
                  <div className={styles.itemActions}>
                    <Button variant="outlined" size="s" onClick={() => void flipJob(row)}>
                      {row.enabled ? t('assistTurnOff') : t('assistTurnOn')}
                    </Button>
                    <Button variant="dangerOutlined" size="s" onClick={() => removeJob(row)}>
                      {t('adminDelete')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {tab === 'polls' ? (
        <>
          <div className={styles.hint}>{t('assistPollHint')}</div>
          <div className={styles.formCard}>
            <TextField label={t('assistPollTitle')} value={pollTitle} onChange={setPollTitle} />
            <div className={styles.inlineFields}>
              <NumberField
                label={t('assistPollProbability')}
                value={pollProbability}
                onChange={setPollProbability}
              />
              <NumberField label={t('assistPollEveryN')} value={pollEveryN} onChange={setPollEveryN} />
            </div>
            <NumberField
              label={t('assistPollActiveHours')}
              value={pollActiveHours}
              onChange={setPollActiveHours}
            />
            <div className={styles.actions}>
              <Button size="s" disabled={busy === 'pollAdd'} onClick={() => void addTick()}>
                {t('assistPollAdd')}
              </Button>
            </div>
          </div>
          {pollRows.length === 0 ? (
            <PanelEmpty text={t('assistPollEmpty')} />
          ) : (
            <div className={styles.list}>
              {pollRows.map((row) => (
                <div key={row.id} className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemName}>{row.title}</div>
                    <div className={styles.itemMeta}>
                      {row.probability}% · {row.wake_every_n} · {row.active_hours}h ·
                      {' '}
                      {t('assistPollTicks', { count: row.ticks_since_fire })}
                    </div>
                  </div>
                  <div className={styles.itemActions}>
                    <Button variant="outlined" size="s" onClick={() => void flipPoll(row)}>
                      {row.enabled ? t('assistTurnOff') : t('assistTurnOn')}
                    </Button>
                    <Button variant="dangerOutlined" size="s" onClick={() => removePoll(row)}>
                      {t('adminDelete')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {tab === 'loop' && isAdmin ? (
        <>
          <div className={styles.hint}>{t('assistLoopHint')}</div>
          <div className={styles.formCard}>
            <TextField label={t('assistLoopTask')} value={loopTask} onChange={setLoopTask} />
            <div className={styles.actions}>
              <Button size="s" disabled={busy === 'loopStart' || !loopTask.trim()} onClick={() => void startRun()}>
                {t('assistLoopStart')}
              </Button>
            </div>
          </div>
          {loopRequests.length > 0 ? (
            <>
              <div className={styles.sectionTitle}>{t('assistLoopRequests')}</div>
              <div className={styles.list}>
                {loopRequests.map((row) => (
                  <div key={row.id} className={styles.item}>
                    <div className={styles.itemMain}>
                      <div className={styles.itemName}>{row.task}</div>
                      <div className={styles.itemMeta}>
                        {row.reason} · {fmtTime(row.created_at, '')}
                      </div>
                    </div>
                    <div className={styles.itemActions}>
                      <Button
                        variant="outlined"
                        size="s"
                        disabled={busy === `loopDecide:${row.id}`}
                        onClick={() => void decideRequest(row, true)}
                      >
                        {t('assistLoopApprove')}
                      </Button>
                      <Button
                        variant="dangerOutlined"
                        size="s"
                        disabled={busy === `loopDecide:${row.id}`}
                        onClick={() => void decideRequest(row, false)}
                      >
                        {t('assistLoopReject')}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}
          <div className={styles.sectionTitle}>{t('assistLoopSessions')}</div>
          {loopRuns.length === 0 ? (
            <PanelEmpty text={t('assistLoopEmpty')} />
          ) : (
            <div className={styles.list}>
              {loopRuns.map((row) => (
                <div key={row.id} className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemName}>
                      {row.task}
                      <span className={styles.badge}>{row.status}</span>
                    </div>
                    <div className={styles.itemMeta}>
                      {t('assistLoopRounds', { count: row.rounds })}
                      {row.result ? ` · ${row.result.slice(0, 120)}` : ''}
                      {row.error ? ` · ${row.error}` : ''}
                    </div>
                  </div>
                  {row.status === 'running' ? (
                    <div className={styles.itemActions}>
                      <Button variant="dangerOutlined" size="s" onClick={() => void stopRun(row)}>
                        {t('assistLoopStop')}
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
          <div className={styles.formCard}>
            <TextField
              label={t('assistLoopInterrupt')}
              hint={t('assistLoopInterruptHint')}
              value={loopInterrupt}
              onChange={setLoopInterrupt}
            />
            <div className={styles.actions}>
              <Button
                variant="outlined"
                size="s"
                disabled={busy === 'loopInterrupt' || !loopInterrupt.trim()}
                onClick={() => void sendInterrupt()}
              >
                {t('assistLoopInterruptSend')}
              </Button>
            </div>
          </div>
        </>
      ) : null}

      {supersedeTarget ? (
        <Modal
          title={t('assistMemSupersede')}
          onClose={() => setSupersedeTarget(null)}
          footer={
            <div className={styles.footer}>
              <Button variant="outlined" onClick={() => setSupersedeTarget(null)}>
                {t('cancel')}
              </Button>
              <Button
                disabled={busy === 'memSupersede' || !supersedeDraft.trim()}
                onClick={() => void confirmSupersede()}
              >
                {t('adminSave')}
              </Button>
            </div>
          }
        >
          <div className={styles.list}>
            <div className={styles.itemMeta}>{supersedeTarget.statement}</div>
          </div>
          <TextField
            label={t('assistMemSupersedeNew')}
            value={supersedeDraft}
            onChange={setSupersedeDraft}
          />
        </Modal>
      ) : null}

      {confirm ? (
        <Modal
          title={confirm.title}
          onClose={() => setConfirm(null)}
          footer={
            <div className={styles.footer}>
              <Button variant="outlined" onClick={() => setConfirm(null)}>
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                disabled={Boolean(busy)}
                onClick={() => {
                  const action = confirm.action
                  setConfirm(null)
                  void run('confirm', action)
                }}
              >
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {confirm.body}
        </Modal>
      ) : null}
    </Modal>
  )
}