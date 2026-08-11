const api = require("./utils/api");

App({
  globalData: {
    activeNovelId: "",
    accessToken: "",
    tokenExpiresAt: 0,
    sessionPromise: null,
    authError: ""
  },

  onLaunch() {
    this.globalData.activeNovelId = wx.getStorageSync("activeNovelId") || "";
    this.globalData.sessionPromise = api.login().then(result => {
      this.globalData.sessionPromise = null;
      return result;
    }).catch(error => {
      this.globalData.sessionPromise = null;
      this.globalData.authError = error.message || "微信登录失败";
      return null;
    });
  }
});
