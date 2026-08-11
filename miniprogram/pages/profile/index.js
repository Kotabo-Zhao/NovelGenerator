const api = require("../../utils/api");

Page({
  data: {
    loggedIn: false,
    authError: "",
    items: [
      { key: "privacy", title: "隐私与数据", desc: "管理作品删除、导出和隐私设置" },
      { key: "voice", title: "朗读设置", desc: "统一管理语音，不在写作页堆叠开关" },
      { key: "help", title: "帮助与反馈", desc: "告诉我们哪里需要变得更好" }
    ]
  },

  onShow() {
    const app = getApp();
    this.setData({
      loggedIn: !!(app.globalData && app.globalData.accessToken),
      authError: app.globalData && app.globalData.authError || ""
    });
  },

  async login() {
    try {
      await api.login();
      this.setData({ loggedIn: true, authError: "" });
      wx.showToast({ title: "登录成功", icon: "success" });
    } catch (error) {
      this.setData({ authError: error.message || "登录失败，请稍后重试" });
    }
  },

  logout() {
    api.logout();
    this.setData({ loggedIn: false, authError: "" });
    wx.showToast({ title: "已退出登录", icon: "none" });
  },

  onTap(event) {
    const item = this.data.items.find(entry => entry.key === event.currentTarget.dataset.key);
    if (item) wx.showToast({ title: item.title + "即将开放", icon: "none" });
  }
});
