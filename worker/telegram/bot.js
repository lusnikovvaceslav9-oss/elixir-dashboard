/** Telegram Bot API helpers + update handler. */

import { getDashboardState } from './data.js';
import { answerQuestion } from './qa.js';
import { buildDigest, buildReport } from './reports.js';

const TG = 'https://api.telegram.org';

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
  if (!raw) return null; // open until configured — /start prints chat id
  return new Set(raw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean));
}

export function isAllowed(env, chatId) {
  const allow = allowedChatIds(env);
  if (!allow) return true;
  return allow.has(String(chatId));
}

export async function handleTelegramUpdate(env, update) {
  const msg = update.message || update.edited_message;
  if (!msg?.chat?.id) return { ok: true, skipped: true };
  const chatId = msg.chat.id;
  const text = String(msg.text || '').trim();
  if (!text) return { ok: true, skipped: true };

  if (!isAllowed(env, chatId)) {
    await sendMessage(env, chatId, `Доступ закрыт.\nТвой chat_id: ${chatId}\nДобавь его в TELEGRAM_ALLOWED_CHAT_IDS на Worker.`);
    return { ok: true, denied: true };
  }

  const cmd = text.split(/\s+/)[0].split('@')[0].toLowerCase();
  const arg = text.replace(/^\S+\s*/, '').trim();

  try {
    if (cmd === '/start') {
      await sendMessage(env, chatId,
        `Elixir Bot 👋\nchat_id: ${chatId}\n\nПиши вопросы как в дашборде:\n«спенд планта за июль»\n\nКоманды:\n/report [проект]\n/digest\n/refresh\n/help`);
      return { ok: true };
    }
    if (cmd === '/help') {
      const state = await getDashboardState(env);
      await sendMessage(env, chatId, answerQuestion('/help', state));
      return { ok: true };
    }
    if (cmd === '/refresh') {
      await getDashboardState(env, { force: true });
      await sendMessage(env, chatId, 'Данные обновлены.');
      return { ok: true };
    }
    if (cmd === '/digest') {
      const state = await getDashboardState(env);
      await sendMessage(env, chatId, buildDigest(state), { parse_mode: 'Markdown' });
      return { ok: true };
    }
    if (cmd === '/report') {
      const state = await getDashboardState(env);
      await sendMessage(env, chatId, buildReport(state, arg), { parse_mode: 'Markdown' });
      return { ok: true };
    }

    // free-text Q&A
    const state = await getDashboardState(env);
    const answer = answerQuestion(text, state);
    await sendMessage(env, chatId, answer);
    return { ok: true };
  } catch (e) {
    try {
      await sendMessage(env, chatId, 'Ошибка: ' + (e.message || e));
    } catch { /* ignore */ }
    return { ok: false, error: e.message || String(e) };
  }
}

export async function sendDigestToAllowed(env) {
  const state = await getDashboardState(env, { force: true });
  const text = buildDigest(state);
  const allow = allowedChatIds(env);
  if (!allow || !allow.size) {
    console.log('digest skipped: no TELEGRAM_ALLOWED_CHAT_IDS');
    return { ok: false, reason: 'no_chats' };
  }
  const results = [];
  for (const chatId of allow) {
    try {
      await sendMessage(env, chatId, text, { parse_mode: 'Markdown' });
      results.push({ chatId, ok: true });
    } catch (e) {
      results.push({ chatId, ok: false, error: e.message });
    }
  }
  return { ok: true, results };
}

export async function setupWebhook(env, workerUrl) {
  const url = `${workerUrl.replace(/\/$/, '')}/telegram/webhook`;
  const secret = env.TELEGRAM_WEBHOOK_SECRET || '';
  const body = { url, allowed_updates: ['message'] };
  if (secret) body.secret_token = secret;
  return tgCall(env, 'setWebhook', body);
}
