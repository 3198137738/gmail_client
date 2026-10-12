import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./styles/fonts.css";
import "./styles/global.css";

createApp(App).use(router).mount("#app");
