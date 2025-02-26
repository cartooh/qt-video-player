import cv2
import sys
import argparse
import time
from PyQt6 import QtWidgets, QtCore, QtGui

class GraphicsView(QtWidgets.QGraphicsView):
    areaSelected = QtCore.pyqtSignal(QtCore.QRectF)

    def __init__(self, *argv, **keywords):
        super(GraphicsView, self).__init__(*argv, **keywords)
        
        self._numScheduledScalings = 0
        self._rectF = QtCore.QRectF(0.0, 0.0, 0.0, 0.0)
    
    def wheelEvent(self, event):
        numDegrees = event.angleDelta().y() / 8
        numSteps = numDegrees / 15
        self._numScheduledScalings += numSteps
        if self._numScheduledScalings * numSteps < 0:
            self._numScheduledScalings = numSteps
        anim = QtCore.QTimeLine(350, self)
        anim.setUpdateInterval(20)
        anim.valueChanged.connect(self._scalingTime)
        anim.finished.connect(self._animFinished)
        anim.start()

    def _scalingTime(self, x):
        factor = 1.0 + float(self._numScheduledScalings) / 300.0
        self.scale(factor, factor)

    def _animFinished(self):
        if self._numScheduledScalings > 0:
            self._numScheduledScalings -= 1
        else:
            self._numScheduledScalings += 1

    def mousePressEvent(self, event):
        
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)

            event = QtGui.QMouseEvent(
                QtCore.QEvent.Type.GraphicsSceneDragMove, 
                event.position(), 
                QtCore.Qt.MouseButton.LeftButton, 
                QtCore.Qt.MouseButton.LeftButton, 
                QtCore.Qt.KeyboardModifier.NoModifier
            )

        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        
        point = self.mapToScene(event.pos())
        self._rectF = QtCore.QRectF(point, point)
        QtWidgets.QGraphicsView.mousePressEvent(self, event)
   
    def mouseReleaseEvent(self, event):
        QtWidgets.QGraphicsView.mouseReleaseEvent(self, event)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.pos())
            self._rectF.setBottomRight(point)
            self.areaSelected.emit(self._rectF)


class VideoPlayerWidget(QtWidgets.QWidget):
    areaSelected = QtCore.pyqtSignal(QtCore.QRectF)
    errorOccurred = QtCore.pyqtSignal(str)
    videoDraw = QtCore.pyqtSignal(float)
    
    def __init__(self):
        super().__init__()
        self._initUI()
        
        # 再生ボタン用
        self._timeLine = QtCore.QTimeLine()
        self._timeLine.valueChanged.connect(self._nextFrameVideo)
        self._timeLine.finished.connect(self._finish)
        # フレーム数を指定して再生すると遅いので、使わない
        # self._timeLine.frameChanged.connect(self._updateFrame)
        # self._timeLine.setEasingCurve(QtCore.QEasingCurve.Linear)
        
        # 動画の情報
        self._video = None
        self._videoFPS = 0
        self._videoWidth = 0
        self._videoHeight = 0
        
        # 動画に重畳表示する際、再描画用のキャッシュ
        self._cache_frame = None
    
    def _initUI(self):
        # openCVの映像を表示
        self._graphicsView = GraphicsView()
        self._graphicsView.setMinimumSize(640, 480)
        self._graphicsView.areaSelected.connect(lambda rectF: self.areaSelected.emit(rectF))
        
        # 再生ボタン
        self._playButton = QtWidgets.QPushButton()
        self._playButton.setEnabled(False)
        self._playButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        self._playButton.clicked.connect(self._play)
        
        # 1フレーム送るボタン
        nextBtn = QtWidgets.QPushButton("≫")
        nextBtn.setEnabled(False)
        nextBtn.setToolTip("Next Frame")
        nextBtn.setStatusTip("Next Frame")
        nextBtn.setFixedSize(24, 24)
        nextBtn.setShortcut(QtCore.Qt.Key.Key_Right)
        nextBtn.clicked.connect(self._nextFrameVideo)
        
        # 1フレーム戻るボタン
        prevBtn = QtWidgets.QPushButton("≪")
        prevBtn.setEnabled(False)
        prevBtn.setToolTip("Previous Frame")
        prevBtn.setStatusTip("Previous Frame")
        prevBtn.setFixedSize(24, 24)
        prevBtn.setShortcut(QtCore.Qt.Key.Key_Left)
        prevBtn.clicked.connect(lambda: self._movePositionSlider(-1))
        
        # 制御ボタン。後でlayoutに追加する用
        ctrlBtn = [self._playButton, prevBtn, nextBtn]
        
        # 送り戻りボタン生成
        for s in [-1, +1, -5, +5]:
            btn = QtWidgets.QPushButton(f"{s:+}s")
            btn.setEnabled(False)
            btn.setToolTip(f"Seek approx. {s:+}s")
            btn.setStatusTip(f"Seek approx. {s:+}s")
            btn.setFixedSize(30, 24)
            btn.clicked.connect(lambda *, s=s: self._movePositionSlider(s * self._videoFPS))
            ctrlBtn += [btn]
        
        # enable/disableを切り替えるため
        self._controlButtons = ctrlBtn
        
        # 現在のフレーム番号表示用
        curFrame = QtWidgets.QLineEdit()
        curFrame.setFixedWidth(70)
        curFrame.setValidator(QtGui.QIntValidator())
        curFrame.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        curFrame.editingFinished.connect(lambda: self._seekPositionSlider(curFrame.text()))
        
        # 現在の経過秒数を表示用
        curSec = QtWidgets.QLineEdit()
        curSec.setFixedWidth(70)
        curSec.setValidator(QtGui.QDoubleValidator(0.00, 1000.00, 3))
        curSec.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        curSec.editingFinished.connect(lambda: self.setCurrentVideoSec(curSec.text()))
        
        # 経過秒数の別表示(コピー用)
        curTime = QtWidgets.QLineEdit()
        # curTime.setReadOnly(True)
        curTime.setFixedWidth(70)
        
        # 全フレーム数、長さ(秒)を表示する用
        endInfo = QtWidgets.QLineEdit()
        endInfo.setFixedWidth(120)
        
        self._curTimeEdit  = curTime
        self._curSecEdit  = curSec
        self._curFrameEdit = curFrame
        self._endInfoEdit  = endInfo
        
        self._lastFrameTime = None
        self._frameCount = 0
        self._totalElapsedTime = 0.0
        self._fpsLabel = QtWidgets.QLabel("FPS: 0")

        self._positionSlider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._positionSlider.setRange(0, 0)
        self._positionSlider.sliderMoved.connect(self._setPosition)
        
        # 動画を開くボタン
        openButton = QtWidgets.QPushButton("Open")
        openButton.setToolTip("Open Video File")
        openButton.setStatusTip("Open Video File")
        openButton.setFixedSize(70, 24)
        openButton.clicked.connect(self.openVideoFile)
        
        # Create layouts to place inside widget
        self.ctrlLayout = QtWidgets.QHBoxLayout()
        self.ctrlLayout.setContentsMargins(0, 0, 0, 0)
        for b in ctrlBtn:
            self.ctrlLayout.addWidget(b)

        self.ctrlLayout.addWidget(curFrame)
        self.ctrlLayout.addWidget(curSec)
        self.ctrlLayout.addWidget(curTime)
        self.ctrlLayout.addWidget(endInfo)
        self.ctrlLayout.addWidget(self._fpsLabel)
        self.ctrlLayout.addStretch()
        self.ctrlLayout.addWidget(openButton)
        
        self.baseLayout = QtWidgets.QVBoxLayout()
        self.baseLayout.addWidget(self._graphicsView)
        self.baseLayout.addLayout(self.ctrlLayout)
        self.baseLayout.addWidget(self._positionSlider)
        
        self.setLayout(self.baseLayout)

    def _initVideo(self):
        if self._video is None:
            return
        
        frame_rate = self._video.get(cv2.CAP_PROP_FPS)
        frame_count = int(self._video.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_time = 1000 / frame_rate
        video_time = frame_count / frame_rate * 1000
        self._pos = 0
        self._sec = 0
        self._videoFPS = frame_rate
        self._videoWidth = int(self._video.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._videoHeight = int(self._video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        self._endInfoEdit.setText(f"{frame_count} frame; {video_time/1000:.3f}s")
        
        self._positionSlider.setMaximum(frame_count)
        self._setPositionSliderValueWithOutSignals(0)
        self._timeLine.setDuration(int(video_time))
        self._timeLine.setUpdateInterval(int(frame_time))
        # self._timeLine.setFrameRange(0, frame_count) # フレーム数を指定して再生すると遅いので、使わない
        
        
        self._graphicsView.setScene( 
            QtWidgets.QGraphicsScene(0, 0, self._videoWidth, self._videoHeight, self._graphicsView) 
        )
        self._graphicsView.scale(0.5, 0.5)
        
        self._updateVideo()
    
    def __setVideoPosFrames(self, pos):
        if self._video is None:
            return
        self._video.set(cv2.CAP_PROP_POS_FRAMES, pos)
    
    def __getVideoPosFrames(self):
        if self._video is None:
            return None
        return int(self._video.get(cv2.CAP_PROP_POS_FRAMES))
    
    def __getVideoPosMSec(self):
        if self._video is None:
            return None
        return self._video.get(cv2.CAP_PROP_POS_MSEC)
    
    def __readVideo(self):
        ret, frame = self._video.read()
        if ret:
            return frame
        else:
            return None
    
    def __drawImage(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).data
        image = QtGui.QImage(rgb, self._videoWidth, self._videoHeight, self._videoWidth*3, QtGui.QImage.Format.Format_RGB888)
        pixmap = QtGui.QPixmap.fromImage(image)
        self._graphicsView.scene().clear()
        self._graphicsView.scene().addPixmap(pixmap)
    
    def _convImage(self, frame):
        return frame
    
    def _redrawVideo(self):
        if self._cache_frame is None:
            return
        
        self.__drawImage(self._convImage(self._cache_frame))
    
    def __drawVideo(self):
        self._cache_frame = self.__readVideo()
        self._redrawVideo()
        self._updateFPS()
    
    def _updateFPS(self):
        currentTime = time.perf_counter()
        if self._lastFrameTime is not None:
            elapsed = currentTime - self._lastFrameTime
            self._totalElapsedTime += elapsed
            self._frameCount += 1
            averageFPS = self._frameCount / self._totalElapsedTime
            self._fpsLabel.setText(f"FPS: {averageFPS:.2f}")
        self._lastFrameTime = currentTime
    
    def _resetFPS(self):
        self._lastFrameTime = None
        self._frameCount = 0
        self._totalElapsedTime = 0.0
        self._fpsLabel.setText("FPS: 0")

    @property
    def pos(self):
        return self._pos
    
    @property
    def sec(self):
        return self._sec
    
    def _setPos(self, pos):
        self._pos = pos
        self._sec = self._pos / self._videoFPS # _setPos は 再生中しか呼ばれないので、0割は無視
        
        self._displayCurrentInfo()
    
    def _displayCurrentInfo(self):
        self._curFrameEdit.setText(str(self._pos))
        self._curSecEdit.setText(f"{self._sec:.3f}")
        
        min, sec = divmod(self._sec, 60)
        hour, min = divmod(min, 60)
        self._curTimeEdit.setText(f"{hour:02.0f}:{min:02.0f}:{sec:06.3f}")
        
    
    def _updateVideo(self, *, pos=None):
        if self._video is None:
            return
        
        if pos is None:
            pos = self.__getVideoPosFrames()
            val = self._positionSlider.value()
            if pos != val:
                print(f"_updateVideo pos != val: {pos=}, {val=}")
        else:
            self.__setVideoPosFrames(pos)
            pos = self.__getVideoPosFrames()
        
        self.__drawVideo()
        self._setPos(pos)
        self.videoDraw.emit(self._sec)
        
    
    def _seekVideo(self, pos):
        if self._video is None:
            return
        
        self._updateVideo(pos=pos)
        #print(pos, self.__getVideoPosFrames(), self._curSecEdit.text(), self.__getVideoPosMSec())
    
    def _setPositionSliderValueWithOutSignals(self, val):
        self._positionSlider.blockSignals(True)
        self._positionSlider.setValue(val)
        self._positionSlider.blockSignals(False)
    
    
    def _updatePositionSlider(self):
        val = self.__getVideoPosFrames()
        self._setPositionSliderValueWithOutSignals(val)
    
    def _nextFrameVideo(self, *args):
        self._updatePositionSlider()
        self._updateVideo()
    
    def _seekPositionSlider(self, seek):
        seek = int(seek)
        self._seekVideo(seek)
        self._positionSlider.setValue(seek)
    
    def _movePositionSlider(self, move):
        val = round(self._positionSlider.value() + move)
        self._seekPositionSlider(val)
    
    
    
    
    # フレーム数を指定して再生すると遅いので、使わない
    # def _updateFrame(self, x):
    #     print(f"_updateFrame: {x}")
    #     self._seekVideo(x)
    #     self._positionSlider.blockSignals(True)
    #     self._positionSlider.setValue(x)
    #     self._positionSlider.blockSignals(False)
    
    def _finish(self):
        #print(f"_finish")
        self._playButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
    
    def setVideoFile(self, fileName):
        if self._video is not None:
            self._video.release()
            self._video = None
        
        self._video = cv2.VideoCapture(fileName)
        if self._video.isOpened():
            for b in self._controlButtons:
                b.setEnabled(True)
            self._initVideo()
        else:
            for b in self._controlButtons:
                b.setEnabled(False)
            self.errorOccurred.emit(f"can't open error: {fileName}")
    
    def openVideoFile(self):
        fileName, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Movie")
        
        if fileName != '':
            self.setVideoFile(fileName)
    
    def _play(self):
        if self._timeLine.state() == QtCore.QTimeLine.State.NotRunning:
            self._resetFPS()
            self._timeLine.start()
            self._playButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPause))
        elif self._timeLine.state() == QtCore.QTimeLine.State.Paused:
            self._resetFPS()
            self._timeLine.setPaused(False)
            self._playButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPause))
        elif self._timeLine.state() == QtCore.QTimeLine.State.Running:
            self._timeLine.setPaused(True)
            self._playButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        else:
            pass
        
    def _setPosition(self, position):
        self._seekVideo(position)
    
    def setCurrentVideoSec(self, sec):
        self._seekPositionSlider(round(float(sec) * self._videoFPS))



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--avi", help="動画ファイル")
    args = parser.parse_args()
    
    app = QtWidgets.QApplication(sys.argv)
    
    window = VideoPlayerWidget()
    window.show()
    if args.avi:
        print(args.avi)
        window.setVideoFile(args.avi)

    app.exec()

if __name__ == '__main__':
    main()



