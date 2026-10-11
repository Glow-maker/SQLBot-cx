<script lang="ts" setup>
import { ref, computed, onUnmounted } from 'vue'
// import Menu from './Menu.vue'
// import custom_small from '@/assets/svg/logo-custom_small.svg'
// import Workspace from './Workspace.vue'
// import Person from './Person.vue'
// import LOGO_fold from '@/assets/LOGO-fold.svg'
// import icon_moments_categories_outlined from '@/assets/svg/icon_moments-categories_outlined.svg'
// import icon_side_fold_outlined from '@/assets/svg/icon_side-fold_outlined.svg'
// import icon_side_expand_outlined from '@/assets/svg/icon_side-expand_outlined.svg'
// import { useRoute, useRouter } from 'vue-router'
// import { useAppearanceStoreWithOut } from '@/stores/appearance'
import { useEmitt } from '@/utils/useEmitt'
import { isMobile } from '@/utils/utils'
import { onBeforeMount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import Menu from './Menu.vue'

const userStore = useUserStore()
const adminRouter = useRouter()
const adminRoute = useRoute()
const showAdminMenu = computed(() => userStore.isAdmin && adminRoute.path.startsWith('/system'))

const isPhone = computed(() => {
  return isMobile()
})
// const router = useRouter()
const collapse = ref(false)
const collapseCopy = ref(false)
// const appearanceStore = useAppearanceStoreWithOut()
let time: any
onUnmounted(() => {
  clearTimeout(time)
})
// const loginBg = computed(() => {
//   return appearanceStore.getLogin
// })
const handleCollapseChange = (val: any = true) => {
  collapseCopy.value = val
  clearTimeout(time)
  time = setTimeout(() => {
    collapse.value = val
  }, 100)
}
useEmitt({
  name: 'collapse-change',
  callback: handleCollapseChange,
})
// const handleFoldExpand = () => {
//   handleCollapseChange(!collapse.value)
// }

// const toWorkspace = () => {
//   router.push('/')
// }

// const toChatIndex = () => {
//   router.push('/chat/index')
// }

// const toUserIndex = () => {
//   router.push('/system/user')
// }
// const route = useRoute()
// const showSysmenu = computed(() => {
//   return route.path.includes('/system')
// })
onBeforeMount(() => {
  if (isPhone.value) {
    collapse.value = true
    collapseCopy.value = true
  }
})
</script>

<template>
  <div class="system-layout" :class="{ 'admin-layout': showAdminMenu }">
    <aside v-if="showAdminMenu" class="admin-sidebar" aria-label="系统管理菜单">
      <Menu :collapse="false" />
    </aside>
    <!-- <div class="left-side" :class="collapse && 'left-side-collapse'">
      <template v-if="showSysmenu">
        <div class="sys-management" @click="toUserIndex">
          <img
            v-if="loginBg"
            :style="{ marginLeft: collapse ? '5px' : 0 }"
            height="30"
            width="30"
            :src="loginBg"
            :class="!collapse && 'collapse-icon'"
            alt=""
            @click="toChatIndex"
          />
          <custom_small
            v-else-if="appearanceStore.themeColor !== 'default'"
            :style="{ marginLeft: collapse ? '5px' : 0 }"
            :class="!collapse && 'collapse-icon'"
          ></custom_small>
          <LOGO_fold
            v-else
            :style="{ marginLeft: collapse ? '5px' : 0 }"
            :class="!collapse && 'collapse-icon'"
          ></LOGO_fold>
          <span v-if="!collapse">{{ $t('training.system_management') }}</span>
        </div>
      </template>
      <template v-else>
        <template v-if="appearanceStore.isBlue">
          <img
            v-if="loginBg && collapse"
            style="margin: 0 0 6px 5px; cursor: pointer"
            height="30"
            width="30"
            :src="loginBg"
            alt=""
            @click="toChatIndex"
          />
          <div v-else-if="loginBg && !collapse" class="default-sqlbot">
            <img
              height="30"
              width="30"
              :src="loginBg"
              alt=""
              class="collapse-icon"
              @click="toChatIndex"
            />
            <span style="max-width: 150px" :title="appearanceStore.name" class="ellipsis">{{
              appearanceStore.name
            }}</span>
          </div>
          <custom_small
            v-else-if="collapse"
            :style="{ marginLeft: collapse ? '5px' : 0 }"
            :class="!collapse && 'collapse-icon'"
          ></custom_small>

          <div v-else class="default-sqlbot">
            <custom_small class="collapse-icon"></custom_small>
            <span style="max-width: 150px" :title="appearanceStore.name" class="ellipsis">{{
              appearanceStore.name
            }}</span>
          </div>
        </template>
        <template v-else-if="appearanceStore.themeColor === 'custom'">
          <img
            v-if="loginBg && collapse"
            style="margin: 0 0 6px 5px; cursor: pointer"
            height="30"
            width="30"
            :src="loginBg"
            alt=""
            @click="toChatIndex"
          />
          <div v-else-if="loginBg && !collapse" class="default-sqlbot">
            <img
              height="30"
              width="30"
              :src="loginBg"
              alt=""
              class="collapse-icon"
              @click="toChatIndex"
            />
            <span style="max-width: 150px" :title="appearanceStore.name" class="ellipsis">{{
              appearanceStore.name
            }}</span>
          </div>
          <custom_small
            v-else-if="collapse"
            style="margin: 0 0 6px 5px; cursor: pointer"
            @click="toChatIndex"
          ></custom_small>
          <div v-else class="default-sqlbot">
            <custom_small class="collapse-icon"></custom_small>
            <span style="max-width: 150px" :title="appearanceStore.name" class="ellipsis">{{
              appearanceStore.name
            }}</span>
          </div>
        </template>
        <template v-else>
          <img
            v-if="loginBg && collapse"
            style="margin: 0 0 6px 5px; cursor: pointer"
            height="30"
            width="30"
            :src="loginBg"
            alt=""
            @click="toChatIndex"
          />
          <div v-else-if="loginBg && !collapse" class="default-sqlbot">
            <img
              height="30"
              width="30"
              :src="loginBg"
              alt=""
              class="collapse-icon"
              @click="toChatIndex"
            />
            <span style="max-width: 150px" :title="appearanceStore.name" class="ellipsis">{{
              appearanceStore.name
            }}</span>
          </div>
          <LOGO_fold
            v-else-if="collapse"
            style="margin: 0 0 6px 5px; cursor: pointer"
            @click="toChatIndex"
          ></LOGO_fold>
          <div v-else class="default-sqlbot">
            <LOGO_fold class="collapse-icon" @click="toChatIndex"></LOGO_fold>
            <span style="max-width: 150px" :title="appearanceStore.name" class="ellipsis">{{
              appearanceStore.name
            }}</span>
          </div>
        </template>
      </template>
      <Workspace v-if="!showSysmenu" :collapse="collapse"></Workspace>
      <Menu :collapse="collapseCopy"></Menu>
      <div class="bottom">
        <div
          v-if="showSysmenu"
          class="back-to_workspace"
          :class="collapse && 'collapse'"
          @click="toWorkspace"
        >
          <el-icon size="18">
            <icon_moments_categories_outlined></icon_moments_categories_outlined>
          </el-icon>
          {{ collapse ? '' : $t('workspace.return_to_workspace') }}
        </div>
        <div class="personal-info">
          <Person :collapse="collapse" :in-sysmenu="showSysmenu"></Person>
          <el-icon size="20" class="fold" @click="handleFoldExpand">
            <icon_side_expand_outlined v-if="collapse"></icon_side_expand_outlined>
            <icon_side_fold_outlined v-else></icon_side_fold_outlined>
          </el-icon>
        </div>
      </div>
    </div> -->
    <div
      class="right-main"
      :class="{ 'right-side-collapse': collapse, 'with-admin-tools': userStore.isAdmin }"
    >
      <nav v-if="userStore.isAdmin" class="admin-toolbar" aria-label="管理员配置入口">
        <el-button v-if="showAdminMenu" text @click="adminRouter.push('/chat/index')"
          >返回问数</el-button
        >
        <el-button v-else text @click="adminRouter.push('/system/model')">系统配置</el-button>
      </nav>
      <div class="content">
        <router-view />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.system-layout {
  width: 100vw;
  height: 100vh;
  background-color: #f1f4f3;
  display: flex;

  .admin-sidebar {
    flex: 0 0 220px;
    padding: 16px;
    overflow-y: auto;
  }

  &.admin-layout {
    background: #f5f7fb;

    .admin-sidebar {
      box-sizing: border-box;
      flex-basis: 216px;
      padding: 56px 12px 24px;
      border-right: 1px solid #e5eaf2;
      background: #f8fafc;

      :deep(.ed-menu) {
        margin: 0;
      }
      :deep(.ed-menu-item),
      :deep(.ed-sub-menu__title) {
        padding-left: 14px !important;
        margin-bottom: 6px;
      }
      :deep(.ed-menu-item.is-active) {
        background: #eaf2ff !important;
        color: #2463d6;
        font-weight: 600;
      }
    }

    .right-main {
      box-sizing: border-box;
      padding: 12px 24px 24px;
    }

    .admin-toolbar {
      padding: 0 0 12px;
    }
    .content {
      box-sizing: border-box;
      border: 1px solid #e5eaf2;
      box-shadow: none;
    }

    @media (max-width: 900px) {
      .admin-sidebar {
        flex-basis: 176px;
        padding-inline: 8px;
      }
      .right-main {
        padding: 12px;
      }
    }
    @media (max-width: 600px) {
      flex-direction: column;
      .admin-sidebar {
        flex: 0 0 auto;
        max-height: 176px;
        padding: 8px;
        border-right: 0;
        border-bottom: 1px solid #e5eaf2;
      }
      .right-main {
        min-height: 0;
      }
    }
  }

  .admin-toolbar {
    display: flex;
    justify-content: flex-start;
    padding: 0 8px 8px;
  }

  .right-main.with-admin-tools {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;

    .content {
      flex: 1;
      min-height: 0;
      height: auto;
    }
  }

  @keyframes rotate {
    0% {
      width: 240px;
    }
    100% {
      width: 64px;
    }
  }

  .left-side {
    width: 240px;
    height: 100%;
    padding: 16px;
    position: relative;
    min-width: 240px;

    .default-sqlbot {
      display: flex;
      align-items: center;
      font-weight: 500;
      font-size: 16px;
      cursor: pointer;
      margin-bottom: 12px;
      .collapse-icon {
        margin-right: 8px;
      }
    }

    .sys-management {
      display: flex;
      align-items: center;
      font-weight: 500;
      font-size: 16px;
      cursor: pointer;
      margin-bottom: 12px;
      .collapse-icon {
        margin-right: 8px;
      }
    }

    .bottom {
      position: absolute;
      bottom: 20px;
      left: 16px;
      font-weight: 400;
      font-size: 14px;
      line-height: 22px;
      width: calc(100% - 32px);
      .back-to_workspace {
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 6px;
        height: 40px;
        cursor: pointer;

        &:not(.collapse) {
          background: #1f23290a;
          border: 1px solid #d9dcdf;
        }
        &:hover {
          background-color: #1f23291a;
        }
        &:active {
          background-color: #1f232926;
        }
        .ed-icon {
          margin-right: 4.95px;
        }
      }

      .personal-info {
        display: flex;
        align-items: center;
        margin-top: 16px;

        .fold {
          cursor: pointer;
          margin-left: auto;
          border-radius: 6px;
          width: 40px;
          height: 40px;
          &:hover,
          &:focus {
            background: #1f23291a;
          }

          &:active {
            background: #1f232933;
          }
        }
      }
    }

    &.left-side-collapse {
      width: 64px;
      min-width: 64px;
      padding: 16px 12px;
      // animation: rotate 0.1s ease-in-out;

      .ed-menu--collapse {
        --ed-menu-icon-width: 32px;
        width: 40px;
      }

      .bottom {
        left: 12px;
        width: calc(100% - 24px);
        .ed-icon {
          margin-right: 0;
        }
      }

      .personal-info {
        flex-wrap: wrap;

        .default-avatar {
          margin: 0 0 26px 4px;
        }

        .fold {
          margin: 0 auto;
        }
      }
    }
  }

  .right-main {
    // width: calc(100% - 240px);
    width: 100%;
    padding: 8px 8px 8px 0;
    max-height: 100vh;

    &.right-side-collapse {
      width: calc(100% - 64px);
    }

    .content {
      width: 100%;
      height: 100%;
      padding: 16px 24px;
      background-color: #fff;
      border-radius: 12px;
      box-shadow: 0px 2px 4px 0px #1f23291f;
      overflow-x: auto;

      &:has(.no-padding) {
        padding: 0;
      }
    }
  }
}
</style>
