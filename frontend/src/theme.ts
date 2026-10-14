import type { ThemeConfig } from 'antd'
import { theme as antdTheme } from 'antd'

/** 「深海链路」暗色主题：延续原控制台的墨蓝底 + 长春花蓝主色 */
export const appTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: '#7e96ff',
    colorInfo: '#7e96ff',
    colorSuccess: '#45c98a',
    colorWarning: '#e0b25a',
    colorError: '#f2706b',
    colorBgBase: '#0b0e15',
    colorBgContainer: '#11151f',
    colorBgElevated: '#181e2b',
    colorBorder: '#303950',
    colorBorderSecondary: '#222938',
    colorText: '#e3e9f4',
    colorTextSecondary: '#93a0b8',
    colorTextTertiary: '#5c6880',
    borderRadius: 8,
    fontSize: 13,
  },
  components: {
    Layout: {
      siderBg: '#10141c',
      headerBg: '#10141c',
      bodyBg: '#0b0e15',
    },
    Menu: {
      itemBg: 'transparent',
      darkItemBg: 'transparent',
      groupTitleFontSize: 11,
    },
    Table: {
      headerBg: '#181e2b',
      headerColor: '#93a0b8',
      rowHoverBg: 'rgba(126, 150, 255, 0.045)',
      borderColor: '#222938',
      cellPaddingBlock: 10,
    },
    Card: {
      colorBgContainer: '#11151f',
    },
    Modal: {
      contentBg: '#131824',
      headerBg: '#131824',
    },
  },
}
