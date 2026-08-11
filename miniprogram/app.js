App({
  globalData: {
    activeNovelId: ""
  },

  onLaunch() {
    this.globalData.activeNovelId = wx.getStorageSync("activeNovelId") || "";
  }
});
