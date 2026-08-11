const api = require("../../utils/api");

Page({
  data: {
    novels: [],
    loading: false,
    error: ""
  },

  onShow() {
    this.loadNovels();
  },

  async loadNovels() {
    this.setData({ loading: true, error: "" });
    try {
      const result = await api.get("/v1/novels");
      this.setData({ novels: result.novels || [] });
    } catch (error) {
      this.setData({ error: error.message || "暂时无法加载书架" });
    } finally {
      this.setData({ loading: false });
    }
  },

  goCreate() {
    wx.switchTab({ url: "/pages/create/index" });
  },

  openNovel(event) {
    const novelId = event.currentTarget.dataset.id;
    wx.setStorageSync("activeNovelId", novelId);
    const app = getApp();
    app.globalData.activeNovelId = novelId;
    wx.switchTab({ url: "/pages/write/index" });
  }
});
