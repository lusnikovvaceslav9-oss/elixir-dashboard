/** Telegram Bot API helpers + update handler. */

import { getDashboardState, getStateForQuestion } from './data.js';
import { answerQuestion } from './qa.js';
import {
  buildDigest,
  buildReport,
  collectBudgetExhaustedAlerts,
  formatBudgetExhaustedMessage,
} from './reports.js';
import { listAllRecords, replaceAllRecords } from '../dashboard.js';

const TG = 'https://api.telegram.org';
const BOT_USERNAME = 'elexir_dashbot';

/** Last non-General forum topic per chat (so answers stay in «! Бот», not General). */
const topicByChat = new Map();

export async function tgCall(env, method, body) {
  const res = await fetch(`${TG}/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) throw new Error(data.description || `Telegram ${method} failed`);
  return data.result;
}

export async function sendMessage(env, chatId, text, extra = {}) {
  const chunks = splitMessage(text, 4000);
  let last;
  for (const chunk of chunks) {
    last = await tgCall(env, 'sendMessage', {
      chat_id: chatId,
      text: chunk,
      disable_web_page_preview: true,
      ...extra,
    });
  }
  return last;
}

function rememberTopic(msg) {
  const chatId = msg?.chat?.id;
  const tid = Number(msg?.message_thread_id || msg?.reply_to_message?.message_thread_id || 0);
  // In this forum thread_id=1 (General) is broken for send; skip it
  if (chatId != null && tid && tid !== 1) {
    topicByChat.set(String(chatId), tid);
  }
}

function resolveTopicId(msg, env) {
  const fromMsg = Number(msg?.message_thread_id || msg?.reply_to_message?.message_thread_id || 0);
  if (fromMsg && fromMsg !== 1) return fromMsg;

  const cached = topicByChat.get(String(msg.chat.id));
  if (cached) return cached;

  // Optional secret: "-1003831428588:42,-100111:7"
  const raw = String(env.TELEGRAM_FORUM_TOPICS || '').trim();
  if (raw) {
    for (const part of raw.split(/[\s,]+/)) {
      const [c, t] = part.split(':');
      if (String(c) === String(msg.chat.id) && t) return Number(t);
    }
  }
  return fromMsg && fromMsg !== 1 ? fromMsg : null;
}

/**
 * Always post into the configured/cached forum topic (e.g. «! Бот» = 3286).
 * Do NOT fall back to bare send — that lands in General.
 */
async function reply(env, msg, text, extra = {}) {
  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const topicId = resolveTopicId(msg, env);
  const attempts = [];

  // Topic-only first — proven to work for member bots in this forum
  if (topicId) {
    attempts.push({ ...extra, message_thread_id: topicId });
    if (msg.message_id) {
      attempts.push({ ...extra, message_thread_id: topicId, reply_to_message_id: msg.message_id });
    }
  } else if (msg.message_id) {
    attempts.push({ ...extra, reply_to_message_id: msg.message_id });
  } else {
    attempts.push({ ...extra });
  }

  let lastErr;
  for (const opts of attempts) {
    try {
      const sent = await sendMessage(env, chatId, text, opts);
      if (topicId) topicByChat.set(String(chatId), topicId);
      return sent;
    } catch (e) {
      lastErr = e;
      console.log('send fail', chatId, e.message || e, opts);
    }
  }

  if (userId && String(userId) !== String(chatId)) {
    try {
      await sendMessage(env, userId, text);
      return null;
    } catch (e2) {
      console.log('dm fallback fail', e2.message);
    }
  }
  throw lastErr || new Error('send failed');
}

function splitMessage(text, max) {
  const s = String(text || '');
  if (s.length <= max) return [s];
  const parts = [];
  let rest = s;
  while (rest.length > max) {
    let cut = rest.lastIndexOf('\n', max);
    if (cut < max * 0.5) cut = max;
    parts.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n+/, '');
  }
  if (rest) parts.push(rest);
  return parts;
}

export function allowedChatIds(env) {
  const raw = String(env.TELEGRAM_ALLOWED_CHAT_IDS || '').trim();
  if (!raw) return null;
  return new Set(raw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean));
}

export function isAllowed(env, chatId, userId) {
  const allow = allowedChatIds(env);
  if (!allow) return true;
  if (allow.has(String(chatId))) return true;
  if (userId != null && allow.has(String(userId))) return true;
  return false;
}

function botUsername(env) {
  return String(env.TELEGRAM_BOT_USERNAME || BOT_USERNAME).replace(/^@/, '').toLowerCase();
}

function stripBotMention(text, env) {
  const u = botUsername(env);
  return String(text || '')
    .replace(new RegExp(`@${u}`, 'ig'), ' ')
    .replace(/@\w+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isGroupChat(msg) {
  const t = msg?.chat?.type;
  return t === 'group' || t === 'supergroup';
}

function botWasAdded(msg, env) {
  const u = botUsername(env);
  const members = msg.new_chat_members || [];
  return members.some(m => m.is_bot && (String(m.username || '').toLowerCase() === u || m.id));
}

export async function handleTelegramUpdate(env, update) {
  const msg = update.message || update.edited_message;
  if (!msg?.chat?.id) return { ok: true, skipped: true };

  rememberTopic(msg);

  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const chatType = msg.chat.type || 'private';
  const group = isGroupChat(msg);

  console.log('tg msg', JSON.stringify({
    chatId, userId, chatType,
    thread: msg.message_thread_id,
    is_topic: msg.is_topic_message,
    text: String(msg.text || msg.caption || '').slice(0, 80),
  }));

  if (botWasAdded(msg, env)) {
    const text =
      `Elixir Bot на связи.\nchat_id: ${chatId}\n\n` +
      `Пиши вопросы в топике «! Бот» — отвечу туда же.`;
    try { await reply(env, msg, text); } catch (e) { console.log('greet fail', e.message); }
    return { ok: true, joined: true };
  }

  let text = String(msg.text || msg.caption || '').trim();
  if (!text) return { ok: true, skipped: true };

  if (group) {
    text = stripBotMention(text, env);
    if (!text) return { ok: true, skipped: true };
  }

  if (!isAllowed(env, chatId, userId)) {
    try {
      await reply(env, msg,
        `Доступ закрыт.\nchat_id: ${chatId}${userId ? `\nuser_id: ${userId}` : ''}`);
    } catch (e) { console.log('deny msg fail', e.message); }
    return { ok: true, denied: true };
  }

  const cmd = text.split(/\s+/)[0].split('@')[0].toLowerCase();
  const arg = text.replace(/^\S+\s*/, '').trim();
  const topicId = resolveTopicId(msg, env);

  try {
    if (cmd === '/start' || cmd === '/id') {
      await reply(env, msg,
        `Elixir Bot 👋\nchat: ${chatType}\nchat_id: ${chatId}` +
        `${userId ? `\nuser_id: ${userId}` : ''}` +
        `${topicId ? `\ntopic_id: ${topicId}` : ''}` +
        `\n\nВ форуме без прав админа Telegram не шлёт боту обычный текст.` +
        `\nПиши ответом (reply) на сообщение бота — так всегда доходит.` +
        `\nИли дай боту админку только на чтение топиков.` +
        `\n\nПример: reply → спенд планта за июль`);
      return { ok: true };
    }
    if (cmd === '/help') {
      await reply(env, msg, answerQuestion('/help', await getDashboardState(env)));
      return { ok: true };
    }
    if (cmd === '/refresh') {
      await getDashboardState(env, { force: true });
      await reply(env, msg, 'Данные обновлены.');
      return { ok: true };
    }
    if (cmd === '/digest') {
      // Всегда в личку админу — в группе только короткий ack, чтобы не засорять чат.
      const digest = buildDigest(await getDashboardState(env, { force: true }));
      const dmTargets = adminNotifyChatIds(env);
      const isPrivate = chatType === 'private';
      if (isPrivate || !dmTargets.length) {
        await reply(env, msg, digest, { parse_mode: 'Markdown' });
      } else {
        for (const chatId of dmTargets) {
          await sendMessage(env, chatId, digest, { parse_mode: 'Markdown' });
        }
        await reply(env, msg, 'Отправил дайджест в личку.');
      }
      return { ok: true };
    }
    if (cmd === '/report') {
      await reply(env, msg, buildReport(await getDashboardState(env), arg), { parse_mode: 'Markdown' });
      return { ok: true };
    }

    // Immediate ack in the right topic — proves webhook received the message
    await reply(env, msg, '⏳');

    const state = await getStateForQuestion(env, text);
    const answer = answerQuestion(text, state);
    await reply(env, msg, answer);
    return { ok: true };
  } catch (e) {
    console.log('handler err', e.message || e);
    try {
      await reply(env, msg, 'Ошибка: ' + (e.message || e));
    } catch { /* ignore */ }
    return { ok: false, error: e.message || String(e) };
  }
}

function forumTopicForChat(env, chatId) {
  const cached = topicByChat.get(String(chatId));
  if (cached) return cached;
  const raw = String(env.TELEGRAM_FORUM_TOPICS || '').trim();
  if (!raw) return null;
  for (const part of raw.split(/[\s,]+/)) {
    const [c, t] = part.split(':');
    if (String(c) === String(chatId) && t) return Number(t);
  }
  return null;
}

function adminNotifyChatIds(env) {
  const raw = String(env.ADMIN_NOTIFY_CHAT_IDS || '').trim();
  if (raw) return raw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
  // Fallback: private user ids from allowlist
  const allow = allowedChatIds(env);
  if (!allow) return [];
  return [...allow].filter(id => !String(id).startsWith('-'));
}

/** Persist budgetAlert flags so we don't spam the same exhaustion. */
async function persistBudgetAlertFlags(env, { markExhausted = [], clearIds = [] } = {}) {
  if (!markExhausted.length && !clearIds.length) return;
  const raw = await listAllRecords(env);
  const clearSet = new Set(clearIds);
  const markById = new Map(markExhausted.map(a => [a.projectId, a]));
  const now = new Date().toISOString();
  let changed = false;
  const next = raw.map(p => {
    if (!p || p.id === '_worker' || p.id === '_csv_uploads') return p;
    if (clearSet.has(p.id) && p.budgetAlert) {
      changed = true;
      const { budgetAlert, ...rest } = p;
      return rest;
    }
    const alert = markById.get(p.id);
    if (alert) {
      changed = true;
      return {
        ...p,
        budgetAlert: {
          exhausted: true,
          sentAt: now,
          remainder: alert.remainder,
        },
      };
    }
    return p;
  });
  if (changed) await replaceAllRecords(env, next);
}

/**
 * When live remainder ≤ 0 — DM admin once until budget is topped up again.
 */
export async function notifyBudgetAlerts(env) {
  const state = await getDashboardState(env, { force: true });
  const { alerts, clears } = collectBudgetExhaustedAlerts(state);
  const admins = adminNotifyChatIds(env);
  const results = [];

  if (clears.length) {
    try {
      await persistBudgetAlertFlags(env, { clearIds: clears });
    } catch (e) {
      console.log('budget alert clear fail', e.message || e);
    }
  }

  if (!alerts.length) {
    return { ok: true, sent: 0, cleared: clears.length, results };
  }
  if (!admins.length) {
    console.log('budget alert skipped: no ADMIN_NOTIFY_CHAT_IDS');
    return { ok: false, reason: 'no_admin', alerts: alerts.length };
  }

  for (const alert of alerts) {
    const text = formatBudgetExhaustedMessage(alert);
    for (const chatId of admins) {
      try {
        await sendMessage(env, chatId, text, { parse_mode: 'Markdown' });
        results.push({ chatId, projectId: alert.projectId, ok: true });
      } catch (e) {
        results.push({ chatId, projectId: alert.projectId, ok: false, error: e.message });
      }
    }
  }

  const sentOk = alerts.filter(a => results.some(r => r.projectId === a.projectId && r.ok));
  try {
    await persistBudgetAlertFlags(env, { markExhausted: sentOk });
  } catch (e) {
    console.log('budget alert persist fail', e.message || e);
  }

  return { ok: true, sent: sentOk.length, cleared: clears.length, results };
}

export async function sendDigestToAllowed(env) {
  const state = await getDashboardState(env, { force: true });
  const text = buildDigest(state);

  // Digest → личка: DIGEST_CHAT_IDS / TELEGRAM_DIGEST_CHAT_IDS, иначе ADMIN_NOTIFY.
  // Группы (-100…) больше не используем по умолчанию.
  const digestRaw = String(env.TELEGRAM_DIGEST_CHAT_IDS || env.DIGEST_CHAT_IDS || '').trim();
  let targets = [];
  if (digestRaw) {
    targets = digestRaw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
  } else {
    targets = adminNotifyChatIds(env);
  }
  // На всякий случай отфильтруем супергруппы, если кто-то снова пропишет -100…
  // (явный TELEGRAM_DIGEST_CHAT_IDS с группой всё ещё можно форсировать через secret).
  if (!String(env.TELEGRAM_DIGEST_CHAT_IDS || '').trim()) {
    targets = targets.filter(id => !String(id).startsWith('-100'));
  }

  if (!targets.length) {
    console.log('digest skipped: no chat targets');
    return { ok: false, reason: 'no_chats' };
  }

  // Forum topics: secret TELEGRAM_FORUM_TOPICS or var DIGEST_FORUM_TOPICS
  const forumEnv = {
    ...env,
    TELEGRAM_FORUM_TOPICS: env.TELEGRAM_FORUM_TOPICS || env.DIGEST_FORUM_TOPICS || '',
  };

  const results = [];
  for (const chatId of targets) {
    try {
      const topicId = forumTopicForChat(forumEnv, chatId);
      const opts = { parse_mode: 'Markdown' };
      if (topicId) opts.message_thread_id = topicId;
      await sendMessage(env, chatId, text, opts);
      results.push({ chatId, ok: true, topicId: topicId || null });
    } catch (e) {
      results.push({ chatId, ok: false, error: e.message });
    }
  }

  let budgetAlerts = null;
  try {
    budgetAlerts = await notifyBudgetAlerts(env);
  } catch (e) {
    console.log('budget alerts err', e.message || e);
    budgetAlerts = { ok: false, error: e.message || String(e) };
  }

  return { ok: true, results, budgetAlerts };
}

export async function setupWebhook(env, workerUrl) {
  const url = `${workerUrl.replace(/\/$/, '')}/telegram/webhook`;
  const secret = env.TELEGRAM_WEBHOOK_SECRET || '';
  const body = {
    url,
    allowed_updates: ['message', 'edited_message'],
    drop_pending_updates: false,
  };
  if (secret) body.secret_token = secret;
  return tgCall(env, 'setWebhook', body);
}
