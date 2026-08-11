// 发布前请将 API_BASE 替换为已配置 HTTPS 合法域名。
const API_BASE = "https://YOUR_API_DOMAIN";

function getBaseUrl() {
  // 仅用于开发者工具临时切换接口地址，不保存认证信息。
  return wx.getStorageSync("apiBase") || API_BASE;
}

function request(path, options = {}) {
  const baseUrl = getBaseUrl();
  if (!baseUrl || baseUrl.indexOf("YOUR_API_DOMAIN") >= 0) {
    return Promise.reject(new Error("请先配置小程序 API_BASE"));
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
        reject(new Error(response.data && response.data.detail || `请求失败（${response.statusCode}）`));
      },
      fail(error) {
        reject(new Error(error.errMsg || "网络连接失败"));
      }
    });
  });
}

module.exports = {
  get(path, options = {}) {
    return request(path, { ...options, method: "GET" });
  },
  post(path, data = {}, options = {}) {
    return request(path, { ...options, method: "POST", data });
  },
  put(path, data = {}, options = {}) {
    return request(path, { ...options, method: "PUT", data });
  }
};
