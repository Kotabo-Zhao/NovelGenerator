// 发布前请将 API_BASE 替换为已配置 HTTPS 合法域名。
const API_BASE = "https://YOUR_API_DOMAIN";
const LOGIN_PATH = "/api/auth/wechat-login";

function getAppState() {
  const app = getApp();
  app.globalData = app.globalData || {};
  return app.globalData;
}

function getBaseUrl() {
  // 仅用于开发者工具临时切换接口地址，不保存认证信息。
  const baseUrl = (wx.getStorageSync("apiBase") || API_BASE || "").replace(/\/$/, "");
  const isHttps = /^https:\/\//i.test(baseUrl);
  const isLocalDev = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(baseUrl);
  if (!isHttps && !isLocalDev) {
    throw new Error("API_BASE 必须使用 HTTPS 合法域名");
  }
  if (baseUrl.indexOf("YOUR_API_DOMAIN") >= 0) {
    throw new Error("请先配置小程序 API_BASE");
  }
  return baseUrl;
}

function rawRequest(path, options = {}) {
  let baseUrl;
  try {
    baseUrl = getBaseUrl();
  } catch (error) {
    return Promise.reject(error);
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl}${path}`,
      method: options.method || "GET",
      data: options.data || {},
      timeout: options.timeout || 15000,
      header: {
        "content-type": "application/json",
        ...(options.header || {})
      },
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
          return;
        }
        const error = new Error(
          response.data && response.data.detail || `请求失败（${response.statusCode}）`
        );
        error.statusCode = response.statusCode;
        reject(error);
      },
      fail(error) {
        const requestError = new Error(error.errMsg || "网络连接失败");
        requestError.statusCode = 0;
        reject(requestError);
      }
    });
  });
}

function clearSession() {
  const state = getAppState();
  state.accessToken = "";
  state.tokenExpiresAt = 0;
  state.consentRequired = false;
  state.privacyVersion = "";
  state.authError = "";
}

function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success(result) {
        if (!result || !result.code) {
          reject(new Error("微信登录凭证为空"));
          return;
        }
        rawRequest(LOGIN_PATH, {
          method: "POST",
          data: { code: result.code },
          timeout: 10000
        }).then(resolve).catch(reject);
      },
      fail(error) {
        reject(new Error(error.errMsg || "微信登录失败"));
      }
    });
  }).then(result => {
    const state = getAppState();
    state.accessToken = result.access_token || "";
    state.tokenExpiresAt = Date.now() + Math.max(60, Number(result.expires_in || 0) - 60) * 1000;
    state.consentRequired = !!result.consent_required;
    state.privacyVersion = result.privacy_version || "v1";
    state.authError = "";
    if (!state.accessToken) throw new Error("登录响应缺少会话令牌");
    return result;
  }).catch(error => {
    const state = getAppState();
    clearSession();
    state.authError = error.message || "微信登录失败";
    throw error;
  });
}

async function logout() {
  const state = getAppState();
  const token = state.accessToken;
  try {
    if (token) {
      await rawRequest("/api/auth/logout", {
        method: "POST",
        header: { Authorization: `Bearer ${token}` },
        timeout: 8000
      });
    }
  } finally {
    clearSession();
  }
}

function ensureSession() {
  const state = getAppState();
  if (state.accessToken && state.tokenExpiresAt > Date.now() + 30000) {
    return Promise.resolve(state.accessToken);
  }
  if (state.sessionPromise) return state.sessionPromise;
  state.sessionPromise = login().then(result => {
    state.sessionPromise = null;
    return result;
  }, error => {
    state.sessionPromise = null;
    throw error;
  });
  return state.sessionPromise;
}

async function request(path, options = {}) {
  const retry = options._retry !== false;
  if (!options.skipAuth) await ensureSession();
  const state = getAppState();
  const headers = {
    ...(options.header || {})
  };
  if (!options.skipAuth && state.accessToken) {
    headers.Authorization = `Bearer ${state.accessToken}`;
  }

  try {
    return await rawRequest(path, { ...options, header: headers });
  } catch (error) {
    if (error.statusCode === 401 && retry && !options.skipAuth) {
      clearSession();
      await ensureSession();
      return request(path, { ...options, _retry: false });
    }
    throw error;
  }
}

module.exports = {
  login,
  logout,
  ensureSession,
  get(path, options = {}) {
    return request(path, { ...options, method: "GET" });
  },
  post(path, data = {}, options = {}) {
    return request(path, { ...options, method: "POST", data });
  },
  put(path, data = {}, options = {}) {
    return request(path, { ...options, method: "PUT", data });
  },
  delete(path, data = {}, options = {}) {
    return request(path, { ...options, method: "DELETE", data });
  }
};
