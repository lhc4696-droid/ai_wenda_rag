<script setup>
import AppIcon from './AppIcon.vue'

defineProps({ compact: { type: Boolean, default: false } })
</script>

<template>
  <div class="idea-sculpture" :class="{ 'is-compact': compact }" aria-hidden="true">
    <div class="sculpture-stage">
      <div class="sculpture-shadow"></div>
      <div class="sculpture-orbit orbit-back"></div>
      <div class="sculpture-cube">
        <span class="cube-face face-front"><AppIcon name="spark" :size="40" /></span>
        <span class="cube-face face-back"></span>
        <span class="cube-face face-left"></span>
        <span class="cube-face face-right"></span>
        <span class="cube-face face-top"></span>
        <span class="cube-face face-bottom"></span>
      </div>
      <div class="sculpture-orbit orbit-front"></div>
      <span class="sculpture-point point-one"></span>
      <span class="sculpture-point point-two"></span>
      <div v-if="!compact" class="sculpture-note note-chat">
        <span class="note-icon"><AppIcon name="chat" :size="16" /></span>
        <span>
          从一个好问题开始
          <small>让灵感有迹可循</small>
        </span>
      </div>
      <div v-if="!compact" class="sculpture-note note-code">
        <span class="note-code-icon">&lt;/&gt;</span>
        <span class="note-code-lines">
          <i></i>
          <i></i>
          <i></i>
        </span>
        <span class="note-check"><AppIcon name="check" :size="12" /></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.idea-sculpture {
  --sculpture-scale: 1;
  position: relative;
  width: 100%;
  height: 280px;
  pointer-events: none;
}
.sculpture-stage {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 340px;
  height: 270px;
  transform: translate(-50%, -50%) scale(var(--sculpture-scale));
  isolation: isolate;
}
.sculpture-shadow {
  position: absolute;
  left: 82px;
  bottom: 18px;
  width: 190px;
  height: 32px;
  border-radius: 50%;
  background: #7564e44d;
  filter: blur(18px);
}
.sculpture-orbit {
  position: absolute;
  left: 19px;
  top: 53px;
  width: 304px;
  height: 157px;
  border: 1px solid #bdb5ff55;
  border-radius: 50%;
  transform: rotate(-29deg);
}
.orbit-back {
  border-width: 2px;
  border-color: #a8b5fa4d;
  transform: rotate(34deg);
}
.orbit-front {
  border-top-color: transparent;
  border-right-color: #c5beffa6;
  box-shadow: 0 1px 0 #bbb1ff26;
}
.sculpture-cube {
  position: absolute;
  top: 87px;
  left: 124px;
  width: 94px;
  height: 94px;
  transform-style: preserve-3d;
  transform: perspective(650px) rotateX(-23deg) rotateY(34deg) rotateZ(-8deg);
}
.cube-face {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  border: 1px solid #e0dcffb3;
  border-radius: 9px;
  background: linear-gradient(135deg, #d8d2ff8c, #7364d78c);
  box-shadow: inset 0 0 24px #e0d5ff26;
  backface-visibility: hidden;
}
.face-front {
  color: #fff;
  background: linear-gradient(145deg, #b7adffc9, #6c5bceb3);
  transform: translateZ(47px);
}
.face-back {
  transform: rotateY(180deg) translateZ(47px);
}
.face-left {
  transform: rotateY(-90deg) translateZ(47px);
}
.face-right {
  transform: rotateY(90deg) translateZ(47px);
}
.face-top {
  background: linear-gradient(135deg, #f4f0ffcc, #aaa3f699);
  transform: rotateX(90deg) translateZ(47px);
}
.face-bottom {
  transform: rotateX(-90deg) translateZ(47px);
}
.sculpture-point {
  position: absolute;
  width: 9px;
  height: 9px;
  border: 1px solid #e6e1ff;
  border-radius: 50%;
  background: #b5a8ff;
  box-shadow: 0 0 16px #b5a8ff66;
}
.point-one {
  top: 68px;
  right: 61px;
}
.point-two {
  bottom: 54px;
  left: 62px;
  width: 6px;
  height: 6px;
}
.sculpture-note {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid #c9c5f04d;
  border-radius: 12px;
  background: linear-gradient(120deg, #343951f2, #262c41f2);
  box-shadow:
    0 12px 32px #090d2626,
    inset 0 1px 0 #ffffff12;
  color: #f1efff;
  font-size: 11px;
  line-height: 1.7;
}
.note-chat {
  top: 34px;
  left: -5px;
  transform: rotate(-5deg);
}
.sculpture-note small {
  display: block;
  color: #b2b8ce;
  font-size: 9px;
}
.note-icon {
  display: grid;
  place-items: center;
  color: #c7bfff;
}
.note-code {
  right: -4px;
  bottom: 28px;
  width: 152px;
  transform: rotate(6deg);
}
.note-code-icon {
  color: #c8bdff;
  font:
    600 19px Consolas,
    monospace;
}
.note-code-lines {
  display: grid;
  gap: 5px;
  width: 57px;
}
.note-code-lines i {
  height: 3px;
  border-radius: 2px;
  background: #b5abff99;
}
.note-code-lines i:nth-child(2) {
  width: 70%;
  background: #8da9ce99;
}
.note-code-lines i:nth-child(3) {
  width: 85%;
}
.note-check {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #8bb7a929;
  color: #b0e1ce;
}
.is-compact {
  --sculpture-scale: 0.31;
  width: 108px;
  height: 88px;
  margin: 0 auto;
}
.is-compact .sculpture-shadow {
  background: #6c5bd133;
}
.is-compact .sculpture-orbit {
  border-color: #8d80d65e;
}
.is-compact .orbit-front {
  border-top-color: transparent;
}
@media (max-width: 1000px) {
  .idea-sculpture:not(.is-compact) {
    --sculpture-scale: 0.8;
    height: 235px;
  }
}
</style>
