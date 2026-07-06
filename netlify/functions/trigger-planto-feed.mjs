const REPO = 'lusnikovvaceslav9-oss/elixir-dashboard';
const WORKFLOW = 'planto-feed.yml';

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export default async (req) => {
  if (req.method === 'OPTIONS') {
    return { statusCode: 204, headers: cors, body: '' };
  }
  if (req.method !== 'POST') {
    return { statusCode: 405, headers: cors, body: JSON.stringify({ ok: false, error: 'POST only' }) };
  }

  const token = process.env.GH_FEED_TOKEN || process.env.GITHUB_TOKEN;
  if (!token) {
    return {
      statusCode: 503,
      headers: { ...cors, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'GH_FEED_TOKEN not configured on Netlify' }),
    };
  }

  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
        'User-Agent': 'elixir-dashboard-feed-trigger',
      },
      body: JSON.stringify({ ref: 'main' }),
    },
  );

  if (!res.ok) {
    const detail = await res.text();
    return {
      statusCode: res.status,
      headers: { ...cors, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: detail.slice(0, 500) }),
    };
  }

  return {
    statusCode: 202,
    headers: { ...cors, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ok: true, message: 'Planto feed workflow dispatched' }),
  };
};
