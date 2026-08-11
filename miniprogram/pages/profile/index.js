const api = require("../../utils/api");

Page({
  data: {
    loggedIn: false,
    loggingIn: false,
    authError: ""
  },

  onShow() {
    const app = getApp();
    this.setData({
      loggedIn: !!(app.globalData && app.globalData.accessToken),
      authError: app.globalData && app.globalData.authError || ""
    });
  },

  async login() {
    if (this.data.loggingIn) return;
    this.setData({ loggingIn: true, authError: "" });
    try {
      await api.login();
      this.setData({ loggedIn: true, authError: "" });
      wx.showToast({ title: "登录成功", icon: "success" });
    } catch (error) {
      this.setData({ authError: error.message || "登录失败，请稍后重试" });
    } finally {
      this.setData({ loggingIn: false });
    }
  },

  logout() {
    api.logout();
    this.setData({ loggedIn: false, authError: "" });
    wx.showToast({ title: "已退出登录", icon: "none" });
  },

  goShelf() {
    wx.switchTab({ url: "/pages/shelf/index" });
  },

  goCreate() {
    wx.switchTab({ url: "/pages/create/index" });
  }
});
