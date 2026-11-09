Page({
  goToChat() {
    // 使用 redirectTo，这样用户到了对话页，点左上角返回不会又回到启动页
    wx.redirectTo({
      url: '/pages/index/index'
    })
  }
})