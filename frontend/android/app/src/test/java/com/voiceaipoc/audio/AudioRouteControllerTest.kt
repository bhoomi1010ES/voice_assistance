package com.voiceaipoc.audio

import android.media.AudioDeviceInfo
import android.media.AudioManager
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioRouteControllerTest {
    @Test
    fun modeIsAcquiredBeforeFocusOrAudioResourceCreation() {
        val platform = FakePlatform()
        val controller = AudioRouteController(platform)

        val result = controller.acquire(AudioRouteController.Owner.CAPTURE)

        assertTrue(result.succeeded)
        assertEquals(listOf("mode:3", "device:BUILTIN_EARPIECE", "focus"), platform.events)
        result.lease!!.release()
    }

    @Test
    fun sharedLeasesRestoreModeFocusAndDeviceOnlyAfterFinalRelease() {
        val platform = FakePlatform()
        val controller = AudioRouteController(platform)
        val capture = controller.acquire(AudioRouteController.Owner.CAPTURE).lease!!
        val playback = controller.acquire(AudioRouteController.Owner.PLAYBACK).lease!!

        capture.release()
        assertEquals(1, controller.getStatus().activeLeaseCount)
        assertEquals(0, platform.abandonFocusCalls)
        assertEquals(AudioRouteController.MODE_IN_COMMUNICATION, platform.currentModeValue)

        playback.release()
        playback.release()
        val status = controller.getStatus()
        assertEquals(0, status.activeLeaseCount)
        assertEquals(0, platform.currentModeValue)
        assertEquals(1, platform.abandonFocusCalls)
        assertEquals(1, platform.clearDeviceCalls)
        assertEquals(1, status.restorationCount)
        assertTrue(status.modeRestored)
        assertTrue(status.audioFocusRestored)
    }

    @Test
    fun failedInitializationRestoresModeFocusAndDevice() {
        val platform = FakePlatform(focusResult = AudioManager.AUDIOFOCUS_REQUEST_FAILED)
        val controller = AudioRouteController(platform)

        val result = controller.acquire(AudioRouteController.Owner.CAPTURE)

        assertFalse(result.succeeded)
        assertEquals(0, platform.currentModeValue)
        assertEquals(1, platform.clearDeviceCalls)
        assertEquals(1, platform.abandonFocusCalls)
        assertEquals(1, controller.getStatus().restorationCount)
        assertEquals(0, controller.getStatus().activeLeaseCount)
    }

    @Test
    fun twentyReconnectCyclesDoNotLeakRouteState() {
        val platform = FakePlatform()
        val controller = AudioRouteController(platform)

        repeat(20) {
            val capture = controller.acquire(AudioRouteController.Owner.CAPTURE).lease!!
            val playback = controller.acquire(AudioRouteController.Owner.PLAYBACK).lease!!
            capture.release()
            playback.release()
        }

        val status = controller.getStatus()
        assertEquals(0, status.activeLeaseCount)
        assertEquals(20, status.restorationCount)
        assertEquals(20, platform.abandonFocusCalls)
        assertEquals(20, platform.clearDeviceCalls)
        assertEquals(0, platform.currentModeValue)
    }

    @Test
    fun focusLossIsForwardedToTtsWithoutReleasingCaptureLease() {
        val platform = FakePlatform()
        val controller = AudioRouteController(platform)
        val losses = AtomicInteger(0)
        controller.registerFocusLossListener(object : AudioRouteController.FocusLossListener {
            override fun onAudioFocusLost(change: Int) {
                losses.incrementAndGet()
            }
        })
        val capture = controller.acquire(AudioRouteController.Owner.CAPTURE).lease!!
        platform.focusListener!!(AudioManager.AUDIOFOCUS_LOSS)

        assertEquals(1, losses.get())
        assertEquals(1, controller.getStatus().activeLeaseCount)
        assertEquals("LOST", controller.getStatus().audioFocusState)
        capture.release()
    }

    @Test
    fun disabledCommunicationRouteDoesNotTouchPlatformState() {
        val platform = FakePlatform()
        val controller = AudioRouteController(
            platform,
            AudioRouteController.Config(communicationRouteEnabled = false),
        )

        val result = controller.acquire(AudioRouteController.Owner.CAPTURE)

        assertTrue(result.succeeded)
        assertEquals(emptyList<String>(), platform.events)
        assertEquals("DISABLED_BY_POLICY", controller.getStatus().requestedCommunicationDevice)
        assertEquals("DISABLED_BY_POLICY", controller.getStatus().audioFocusState)
        assertFalse(controller.getStatus().communicationRouteEnabled)
        result.lease!!.release()
        assertEquals(0, platform.currentModeValue)
        assertEquals(0, platform.abandonFocusCalls)
    }

    private class FakePlatform(
        override val sdkInt: Int = 35,
        private val focusResult: Int = AudioManager.AUDIOFOCUS_REQUEST_GRANTED,
    ) : AudioRouteController.Platform {
        var currentModeValue = 0
        var speakerphone = false
        var currentDevice: AudioRouteController.CommunicationDevice? = null
        var focusListener: ((Int) -> Unit)? = null
        var abandonFocusCalls = 0
        var clearDeviceCalls = 0
        val events = mutableListOf<String>()

        private val devices = listOf(
            AudioRouteController.CommunicationDevice(
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
                "speaker",
            ),
            AudioRouteController.CommunicationDevice(
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
                "earpiece",
            ),
        )

        override fun currentMode(): Int = currentModeValue
        override fun setMode(mode: Int) {
            currentModeValue = mode
            events += "mode:$mode"
        }
        override fun requestAudioFocus(listener: (Int) -> Unit): Int {
            events += "focus"
            focusListener = listener
            return focusResult
        }
        override fun abandonAudioFocus(listener: (Int) -> Unit): Int {
            abandonFocusCalls += 1
            return AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        }
        override fun availableCommunicationDevices() = devices
        override fun currentCommunicationDevice() = currentDevice
        override fun setCommunicationDevice(device: AudioRouteController.CommunicationDevice): Boolean {
            currentDevice = device
            events += "device:${if (device.type == AudioDeviceInfo.TYPE_BUILTIN_EARPIECE) "BUILTIN_EARPIECE" else "BUILTIN_SPEAKER"}"
            return true
        }
        override fun clearCommunicationDevice() {
            clearDeviceCalls += 1
            currentDevice = null
        }
        override fun isSpeakerphoneOn(): Boolean = speakerphone
        override fun setSpeakerphoneOn(enabled: Boolean) {
            speakerphone = enabled
        }
        override fun startBluetoothSco(): Boolean = false
        override fun stopBluetoothSco() = Unit
        override fun describePlaybackRoute(): String = currentDevice?.name ?: "PLATFORM_DEFAULT"
    }
}
