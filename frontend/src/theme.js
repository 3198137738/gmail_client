// Naive UI 主题：Gmail（Material 3）浅色配色、圆角按钮。
const font = '"Google Sans", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", Arial, sans-serif';

export const themeOverrides = {
  common: {
    fontFamily: font,
    primaryColor: "#0b57d0",
    primaryColorHover: "#0842a0",
    primaryColorPressed: "#062e6f",
    primaryColorSuppl: "#0b57d0",
    infoColor: "#0b57d0",
    infoColorHover: "#0842a0",
    infoColorPressed: "#062e6f",
    infoColorSuppl: "#0b57d0",
    errorColor: "#b3261e",
    errorColorHover: "#8c1d18",
    errorColorPressed: "#601410",
    errorColorSuppl: "#b3261e",
    successColor: "#146c2e",
    successColorHover: "#0f5223",
    successColorPressed: "#0a3818",
    successColorSuppl: "#146c2e",
    textColorBase: "#1f1f1f",
    textColor1: "#1f1f1f",
    textColor2: "#444746",
    textColor3: "#5e5e5e",
    bodyColor: "#f6f8fc",
    borderRadius: "8px",
    borderRadiusSmall: "6px",
  },
  Button: {
    borderRadiusTiny: "14px",
    borderRadiusSmall: "16px",
    borderRadiusMedium: "20px",
    borderRadiusLarge: "24px",
    heightMedium: "36px",
    paddingMedium: "0 20px",
    fontWeight: "500",
  },
  Input: {
    borderRadius: "8px",
    heightMedium: "40px",
  },
  Card: {
    borderRadius: "16px",
  },
  Dialog: {
    borderRadius: "24px",
    padding: "24px",
  },
  Tag: {
    borderRadius: "6px",
  },
  Menu: {
    borderRadius: "0 16px 16px 0",
    itemHeight: "32px",
    itemColorActive: "#d3e3fd",
    itemColorActiveHover: "#d3e3fd",
    itemColorHover: "rgba(68, 71, 70, .08)",
    itemTextColorActive: "#001d35",
    itemTextColorActiveHover: "#001d35",
    itemIconColorActive: "#001d35",
    itemIconColorActiveHover: "#001d35",
    itemTextColor: "#1f1f1f",
    itemIconColor: "#444746",
  },
  Tabs: {
    tabTextColorActiveLine: "#0b57d0",
    barColor: "#0b57d0",
  },
  DataTable: {
    thColor: "#f6f8fc",
  },
};
