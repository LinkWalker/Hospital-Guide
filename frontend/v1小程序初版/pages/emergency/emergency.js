Page({
  call120() {
    wx.makePhoneCall({ phoneNumber: '120' })
  },
  goBack() {
    wx.navigateBack()
  }
})