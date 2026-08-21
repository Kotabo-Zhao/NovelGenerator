const api = require("../../utils/api");

Page({
  data: {
    loggedIn: false,
    loggingIn: false,
    deleting: false,
    usage: null,
    consentRequired: false,
    privacyVersion: "v1",
    authError: ""
  },

  onShow() {
    const app = getApp();
    this.setData({
      loggedIn: !!(app.globalData && app.globalData.accessToken),
      consentRequired: !!(app.globalData && app.globalData.consentRequired),
      privacyVersion: app.globalData && app.globalData.privacyVersion || "v1",
      authError: app.globalData && app.globalData.authError || ""
    });
    if (app.globalData && app.globalData.accessToken && !(app.globalData && app.globalData.consentRequired)) {
      this.loadUsage();
    }
  },

  async loadUsage() {
    try {
      const usage = await api.get("/v1/usage");
      this.setData({ usage });
    } catch (error) {
      this.setData({ usage: null });
    }
  },

  async login() {
    if (this.data.loggingIn) return;
    this.setData({ loggingIn: true, authError: "" });
    try {
      const result = await api.login();
      this.setData({
        loggedIn: true,
        consentRequired: !!result.consent_required,
        privacyVersion: result.privacy_version || "v1",
        authError: ""
      });
      wx.showToast({ title: "登录成功", icon: "success" });
    } catch (error) {
      this.setData({ authError: error.message || "登录失败，请稍后重试" });
    } finally {
      this.setData({ loggingIn: false });
    }
  },

  async logout() {
    try {
      await api.logout();
    } catch (error) {
      this.setData({ authError: error.message || "退出登录失败" });
      return;
    }
    this.setData({ loggedIn: false, consentRequired: false, authError: "" });
    wx.showToast({ title: "已退出登录", icon: "none" });
  },

  async agreePrivacy() {
    try {
      await api.post("/api/auth/consent", {
        policy_version: this.data.privacyVersion,
        agreed: true
      });
      const app = getApp();
      app.globalData.consentRequired = false;
      this.setData({ consentRequired: false });
      wx.showToast({ title: "已同意隐私政策", icon: "success" });
    } catch (error) {
      this.setData({ authError: error.message || "隐私同意失败" });
    }
  },

  deleteAccount() {
    wx.showModal({
      title: "注销账号",
      content: "注销会删除你的作品、任务记录和导出文件，且无法恢复。",
      confirmColor: "#c0392b",
      success: async result => {
        if (!result.confirm || this.data.deleting) return;
        this.setData({ deleting: true, authError: "" });
        try {
          await api.delete("/api/auth/account");
          // 注销后 token 已被服务端吊销，再调 logout 必 401；
          // 直接清本地会话态即可，避免 UI 报错 + loggedIn 残留。
          api.clearSession();
          this.setData({ loggedIn: false, consentRequired: false, authError: "" });
          wx.showToast({ title: "账号已注销", icon: "success" });
        } catch (error) {
          this.setData({ authError: error.message || "注销失败，请先取消进行中的任务" });
        } finally {
          this.setData({ deleting: false });
        }
      }
    });
  },

  goShelf() {
    wx.switchTab({ url: "/pages/shelf/index" });
  },

  goCreate() {
    wx.switchTab({ url: "/pages/create/index" });
  }
});
