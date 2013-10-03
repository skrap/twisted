# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Test cases for L{twisted.python.logger}.
"""

from os import environ
from cStringIO import StringIO
from time import mktime
import logging as py_logging
from inspect import currentframe

try:
    from time import tzset
except ImportError:
    tzset = None

from zope.interface.verify import verifyObject, BrokenMethodImplementation

from twisted.python import log as twistedLogging
from twisted.python.failure import Failure
from twisted.trial import unittest
from twisted.trial.unittest import SkipTest

from twisted.python.logger import (
    LogLevel, InvalidLogLevelError,
    formatEvent, formatUnformattableEvent, formatWithCall,
    Logger, LegacyLogger,
    ILogObserver, LogPublisher, DefaultLogPublisher,
    FilteringLogObserver, PredicateResult,
    FileLogObserver, PythonLogObserver, RingBufferLogObserver,
    LegacyLogObserverWrapper,
    LogLevelFilterPredicate, OBSERVER_REMOVED,
    formatTrace,
)



defaultLogLevel         = LogLevelFilterPredicate().defaultLogLevel
clearLogLevels          = Logger.publisher.levels.clearLogLevels
logLevelForNamespace    = Logger.publisher.levels.logLevelForNamespace
setLogLevelForNamespace = Logger.publisher.levels.setLogLevelForNamespace



class TestLogger(Logger):
    def emit(self, level, format=None, **kwargs):
        def observer(event):
            self.event = event

        Logger.publisher.addObserver(observer, filtered=False)
        try:
            Logger.emit(self, level, format, **kwargs)
        finally:
            Logger.publisher.removeObserver(observer)

        self.emitted = {
            "level":  level,
            "format": format,
            "kwargs": kwargs,
        }



class TestLegacyLogger(LegacyLogger):
    def __init__(self, logger=TestLogger()):
        LegacyLogger.__init__(self, logger=logger)



class LogComposedObject(object):
    """
    Just a regular object.
    """
    log = TestLogger()

    def __init__(self, state=None):
        self.state = state


    def __str__(self):
        return "<LogComposedObject {state}>".format(state=self.state)



class SetUpTearDown(object):
    def setUp(self):
        super(SetUpTearDown, self).setUp()
        clearLogLevels()


    def tearDown(self):
        super(SetUpTearDown, self).tearDown()
        clearLogLevels()



class LoggingTests(SetUpTearDown, unittest.TestCase):
    """
    General module tests.
    """

    def test_levelWithName(self):
        """
        Look up log level by name.
        """
        for level in LogLevel.iterconstants():
            self.assertIdentical(LogLevel.levelWithName(level.name), level)


    def test_levelWithInvalidName(self):
        """
        You can't make up log level names.
        """
        bogus = "*bogus*"
        try:
            LogLevel.levelWithName(bogus)
        except InvalidLogLevelError as e:
            self.assertIdentical(e.level, bogus)
        else:
            self.fail("Expected InvalidLogLevelError.")


    def test_defaultLogLevel(self):
        """
        Default log level is used.
        """
        self.failUnless(logLevelForNamespace(None), defaultLogLevel)
        self.failUnless(logLevelForNamespace(""), defaultLogLevel)
        self.failUnless(logLevelForNamespace("rocker.cool.namespace"),
                        defaultLogLevel)


    def test_setLogLevel(self):
        """
        Setting and retrieving log levels.
        """
        setLogLevelForNamespace(None, LogLevel.error)
        setLogLevelForNamespace("twext.web2", LogLevel.debug)
        setLogLevelForNamespace("twext.web2.dav", LogLevel.warn)

        self.assertEquals(logLevelForNamespace(None),
                          LogLevel.error)
        self.assertEquals(logLevelForNamespace("twisted"),
                          LogLevel.error)
        self.assertEquals(logLevelForNamespace("twext.web2"),
                          LogLevel.debug)
        self.assertEquals(logLevelForNamespace("twext.web2.dav"),
                          LogLevel.warn)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test"),
                          LogLevel.warn)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test1.test2"),
                          LogLevel.warn)


    def test_setInvalidLogLevel(self):
        """
        Can't pass invalid log levels to setLogLevelForNamespace().
        """
        self.assertRaises(InvalidLogLevelError, setLogLevelForNamespace,
                          "twext.web2", object())

        # Level must be a constant, not the name of a constant
        self.assertRaises(InvalidLogLevelError, setLogLevelForNamespace,
                          "twext.web2", "debug")


    def test_clearLogLevels(self):
        """
        Clearing log levels.
        """
        setLogLevelForNamespace("twext.web2", LogLevel.debug)
        setLogLevelForNamespace("twext.web2.dav", LogLevel.error)

        clearLogLevels()

        self.assertEquals(logLevelForNamespace("twisted"), defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2"), defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2.dav"),
                          defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test"),
                          defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test1.test2"),
                          defaultLogLevel)


    def test_namespace_default(self):
        """
        Default namespace is module name.
        """
        log = Logger()
        self.assertEquals(log.namespace, __name__)


    def test_formatWithCall(self):
        """
        L{formatWithCall} is an extended version of L{unicode.format} that will
        interpret a set of parentheses "C{()}" at the end of a format key to
        mean that the format key ought to be I{called} rather than stringified.
        """
        self.assertEquals(
            formatWithCall(
                u"Hello, {world}. {callme()}.",
                dict(world="earth", callme=lambda: "maybe")
            ),
            "Hello, earth. maybe."
        )
        self.assertEquals(
            formatWithCall(
                u"Hello, {repr()!r}.",
                dict(repr=lambda: "repr")
            ),
            "Hello, 'repr'."
        )


    def test_formatEvent(self):
        """
        L{formatEvent} will format an event according to several rules:

            - A string with no formatting instructions will be passed straight
              through.

            - PEP 3101 strings will be formatted using the keys and values of
              the event as named fields.

            - PEP 3101 keys ending with C{()} will be treated as instructions
              to call that key (which ought to be a callable) before
              formatting.

        L{formatEvent} will always return L{unicode}, and if given
        bytes, will always treat its format string as UTF-8 encoded.
        """
        def format(log_format, **event):
            event["log_format"] = log_format
            result = formatEvent(event)
            self.assertIdentical(type(result), unicode)
            return result

        self.assertEquals(u"", format(b""))
        self.assertEquals(u"", format(u""))
        self.assertEquals(u"abc", format("{x}", x="abc"))
        self.assertEquals(u"no, yes.",
                          format("{not_called}, {called()}.",
                                 not_called="no", called=lambda: "yes"))
        self.assertEquals(u'S\xe1nchez', format("S\xc3\xa1nchez"))
        self.assertIn(u"Unable to format event", format(b"S\xe1nchez"))
        self.assertIn(u"Unable to format event",
                      format(b"S{a}nchez", a=b"\xe1"))
        self.assertIn(u"S'\\xe1'nchez",
                      format(b"S{a!r}nchez", a=b"\xe1"))


    def test_formatEventNoFormat(self):
        """
        Formatting an event with no format.
        """
        event = dict(foo=1, bar=2)
        result = formatEvent(event)

        self.assertIn("Unable to format event", result)
        self.assertIn(repr(event), result)


    def test_formatEventWeirdFormat(self):
        """
        Formatting an event with a bogus format.
        """
        event = dict(log_format=object(), foo=1, bar=2)
        result = formatEvent(event)

        self.assertIn("Log format must be unicode or bytes", result)
        self.assertIn(repr(event), result)


    def test_formatUnformattableEvent(self):
        """
        Formatting an event that's just plain out to get us.
        """
        event = dict(log_format="{evil()}", evil=lambda: 1/0)
        result = formatEvent(event)

        self.assertIn("Unable to format event", result)
        self.assertIn(repr(event), result)


    def test_formatUnformattableEventWithUnformattableKey(self):
        """
        Formatting an unformattable event that has an unformattable key.
        """
        event = {
            "log_format": "{evil()}",
            "evil": lambda: 1/0,
            Unformattable(): "gurk",
        }
        result = formatEvent(event)
        self.assertIn("MESSAGE LOST: unformattable object logged:", result)
        self.assertIn("Recoverable data:", result)
        self.assertIn("Exception during formatting:", result)


    def test_formatUnformattableEventWithUnformattableValue(self):
        """
        Formatting an unformattable event that has an unformattable value.
        """
        event = dict(
            log_format="{evil()}",
            evil=lambda: 1/0,
            gurk=Unformattable(),
        )
        result = formatEvent(event)
        self.assertIn("MESSAGE LOST: unformattable object logged:", result)
        self.assertIn("Recoverable data:", result)
        self.assertIn("Exception during formatting:", result)


    def test_formatUnformattableEventWithUnformattableErrorOMGWillItStop(self):
        """
        Formatting an unformattable event that has an unformattable value.
        """
        event = dict(
            log_format="{evil()}",
            evil=lambda: 1/0,
            recoverable="okay",
        )
        # Call formatUnformattableEvent() directly with a bogus exception.
        result = formatUnformattableEvent(event, Unformattable())
        self.assertIn("MESSAGE LOST: unformattable object logged:", result)
        self.assertIn(repr("recoverable") + " = " + repr("okay"), result)



class LoggerTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{Logger}.
    """

    def test_repr(self):
        """
        repr() on Logger
        """
        namespace = "bleargh"
        log = Logger(namespace)
        self.assertEquals(repr(log), "<Logger {0}>".format(repr(namespace)))


    def test_namespace_attribute(self):
        """
        Default namespace for classes using L{Logger} as a descriptor is the
        class name they were retrieved from.
        """
        obj = LogComposedObject()
        self.assertEquals(obj.log.namespace,
                          "twisted.python.test.test_logger.LogComposedObject")
        self.assertEquals(LogComposedObject.log.namespace,
                          "twisted.python.test.test_logger.LogComposedObject")
        self.assertIdentical(LogComposedObject.log.source, LogComposedObject)
        self.assertIdentical(obj.log.source, obj)
        self.assertIdentical(Logger().source, None)


    def test_sourceAvailableForFormatting(self):
        """
        On instances that have a L{Logger} class attribute, the C{log_source}
        key is available to format strings.
        """
        obj = LogComposedObject("hello")
        log = obj.log
        log.error("Hello, {log_source}.")

        self.assertIn("log_source", log.event)
        self.assertEquals(log.event["log_source"], obj)

        stuff = formatEvent(log.event)
        self.assertIn("Hello, <LogComposedObject hello>.", stuff)


    def test_basic_Logger(self):
        """
        Test that log levels and messages are emitted correctly for
        Logger.
        """
        # FIXME: Need a basic test like this for logger attached to a class.
        # At least: source should not be None in that case.

        log = TestLogger()

        for level in LogLevel.iterconstants():
            format = "This is a {level_name} message"
            message = format.format(level_name=level.name)

            log_method = getattr(log, level.name)
            log_method(format, junk=message, level_name=level.name)

            # Ensure that test_emit got called with expected arguments
            self.assertEquals(log.emitted["level"], level)
            self.assertEquals(log.emitted["format"], format)
            self.assertEquals(log.emitted["kwargs"]["junk"], message)

            self.assertTrue(hasattr(log, "event"), "No event observed.")

            self.assertEquals(log.event["log_format"], format)
            self.assertEquals(log.event["log_level"], level)
            self.assertEquals(log.event["log_namespace"], __name__)
            self.assertEquals(log.event["log_source"], None)
            self.assertEquals(log.event["junk"], message)

            self.assertEquals(formatEvent(log.event), message)


    def test_defaultFailure(self):
        """
        Test that log.failure() emits the right data.
        """
        log = TestLogger()
        try:
            raise RuntimeError("baloney!")
        except RuntimeError:
            log.failure("Whoops")

        errors = self.flushLoggedErrors(RuntimeError)
        self.assertEquals(len(errors), 1)

        self.assertEquals(log.emitted["level"], LogLevel.error)
        self.assertEquals(log.emitted["format"], "Whoops")


    def test_conflicting_kwargs(self):
        """
        Make sure that kwargs conflicting with args don't pass through.
        """
        log = TestLogger()

        log.warn(
            u"*",
            log_format="#",
            log_level=LogLevel.error,
            log_namespace="*namespace*",
            log_source="*source*",
        )

        # FIXME: Should conflicts log errors?

        self.assertEquals(log.event["log_format"], u"*")
        self.assertEquals(log.event["log_level"], LogLevel.warn)
        self.assertEquals(log.event["log_namespace"], log.namespace)
        self.assertEquals(log.event["log_source"], None)


    def test_logInvalidLogLevel(self):
        """
        Test passing in a bogus log level to C{emit()}.
        """
        log = TestLogger()

        log.emit("*bogus*")

        errors = self.flushLoggedErrors(InvalidLogLevelError)
        self.assertEquals(len(errors), 1)


    def test_trace(self):
        """
        Tracing keeps track of forwarding to the publisher.
        """
        log = TestLogger()

        def publisher(event):
            observer(event)

        def observer(event):
            self.assertEquals(event["log_trace"], [(log, publisher)])

        log.publisher = publisher
        log.info("Hello.", log_trace=[])



class LogPublisherTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{LogPublisher}.
    """

    def test_interface(self):
        """
        L{LogPublisher} is an L{ILogObserver}.
        """
        publisher = LogPublisher()
        try:
            verifyObject(ILogObserver, publisher)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def test_observers(self):
        """
        L{LogPublisher.observers} returns the observers.
        """
        o1 = lambda e: None
        o2 = lambda e: None

        publisher = LogPublisher(o1, o2)
        self.assertEquals(set((o1, o2)), set(publisher.observers))


    def test_addObserver(self):
        """
        L{LogPublisher.addObserver} adds an observer.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = LogPublisher(o1, o2)
        publisher.addObserver(o3)
        self.assertEquals(set((o1, o2, o3)), set(publisher.observers))


    def test_addObserverNotCallable(self):
        """
        L{LogPublisher.addObserver} refuses to add an observer that's
        not callable.
        """
        publisher = LogPublisher()
        self.assertRaises(TypeError, publisher.addObserver, object())


    def test_removeObserver(self):
        """
        L{LogPublisher.removeObserver} removes an observer.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = LogPublisher(o1, o2, o3)
        publisher.removeObserver(o2)
        self.assertEquals(set((o1, o3)), set(publisher.observers))


    def test_removeObserverNotRegistered(self):
        """
        L{LogPublisher.removeObserver} removes an observer that is not
        registered.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = LogPublisher(o1, o2)
        publisher.removeObserver(o3)
        self.assertEquals(set((o1, o2)), set(publisher.observers))


    def test_fanOut(self):
        """
        L{LogPublisher} calls its observers.
        """
        event = dict(foo=1, bar=2)

        events1 = []
        events2 = []
        events3 = []

        o1 = lambda e: events1.append(e)
        o2 = lambda e: events2.append(e)
        o3 = lambda e: events3.append(e)

        publisher = LogPublisher(o1, o2, o3)
        publisher(event)
        self.assertIn(event, events1)
        self.assertIn(event, events2)
        self.assertIn(event, events3)


    def test_observerRaises(self):
        """
        Observer raises an exception during fan out: a failure should be
        logged, but not re-raised.  Life goes on.
        """
        nonTestEvents = []
        Logger.publisher.addObserver(lambda e: nonTestEvents.append(e))

        event = dict(foo=1, bar=2)
        exception = RuntimeError("ARGH! EVIL DEATH!")

        events = []

        def observer(event):
            events.append(event)
            raise exception

        publisher = LogPublisher(observer)
        publisher(event)

        # Verify that the observer saw my event
        self.assertIn(event, events)

        # Verify that the observer raised my exception
        errors = self.flushLoggedErrors(exception.__class__)
        self.assertEquals(len(errors), 1)
        self.assertIdentical(errors[0].value, exception)

        # Verify that the exception was logged
        for event in nonTestEvents:
            if (
                event.get("log_format", None) == OBSERVER_REMOVED and
                getattr(event.get("failure", None), "value") is exception
            ):
                break
        else:
            self.fail("Observer raised an exception "
                      "and the exception was not logged.")


    def test_observerRaisesAndLoggerHatesMe(self):
        """
        Observer raises an exception during fan out and thge publisher's Logger
        pukes when the failure is reported.  Exception should still not
        propagate back to the caller.
        """
        nonTestEvents = []
        Logger.publisher.addObserver(lambda e: nonTestEvents.append(e))

        event = dict(foo=1, bar=2)
        exception = RuntimeError("ARGH! EVIL DEATH!")

        def observer(event):
            raise RuntimeError("Sad panda")

        class GurkLogger(Logger):
            def failure(self, *args, **kwargs):
                raise exception

        publisher = LogPublisher(observer)
        publisher.log = GurkLogger()
        publisher(event)

        # Here, the lack of an exception thus far is a success, of sorts


    def test_trace(self):
        """
        Tracing keeps track of forwarding done by the publisher.
        """
        publisher = LogPublisher()

        event = dict(log_trace=[])

        o1 = lambda e: None

        def o2(e):
            self.assertIdentical(e, event)
            self.assertEquals(
                e["log_trace"],
                [
                    (publisher, o1),
                    (publisher, o2),
                    # Event hasn't been sent to o3 yet
                ]
            )

        def o3(e):
            self.assertIdentical(e, event)
            self.assertEquals(
                e["log_trace"],
                [
                    (publisher, o1),
                    (publisher, o2),
                    (publisher, o3),
                ]
            )

        publisher.addObserver(o1)
        publisher.addObserver(o2)
        publisher.addObserver(o3)
        publisher(event)


    def test_formatTrace(self):
        """
        Format trace as string.
        """
        event = dict(log_trace=[])

        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None
        o4 = lambda e: None
        o5 = lambda e: None

        o1.name = "root/o1"
        o2.name = "root/p1/o2"
        o3.name = "root/p1/o3"
        o4.name = "root/p1/p2/o4"
        o5.name = "root/o5"

        def testObserver(e):
            self.assertIdentical(e, event)
            trace = formatTrace(e["log_trace"])
            self.assertEquals(
                trace,
                (
                    "{root} ({root.name})\n" +
                    "  -> {o1} ({o1.name})\n" +
                    "  -> {p1} ({p1.name})\n" +
                    "    -> {o2} ({o2.name})\n" +
                    "    -> {o3} ({o3.name})\n" +
                    "    -> {p2} ({p2.name})\n" +
                    "      -> {o4} ({o4.name})\n" +
                    "  -> {o5} ({o5.name})\n" +
                    "  -> {oTest}\n"
                ).format(
                        root=root,
                        o1=o1, o2=o2, o3=o3, o4=o4, o5=o5,
                        p1=p1, p2=p2,
                        oTest=oTest
                )
            )
        oTest = testObserver

        p2 = LogPublisher(o4)
        p1 = LogPublisher(o2, o3, p2)

        p2.name = "root/p1/p2/"
        p1.name = "root/p1/"

        root = LogPublisher(o1, p1, o5, oTest)
        root.name = "root/"
        root(event)



class DefaultLogPublisherTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{DefaultLogPublisher}.
    """

    def test_addObserver(self):
        """
        L{DefaultLogPublisher.addObserver} adds an observer.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = DefaultLogPublisher()
        publisher.addObserver(o1)
        publisher.addObserver(o2, filtered=True)
        publisher.addObserver(o3, filtered=False)

        self.assertEquals(
            set((o1, o2)),
            set(publisher.filteredPublisher.observers),
            "Filtered observers do not match expected set"
        )
        self.assertEquals(
            set((o3, publisher.filters)),
            set(publisher.rootPublisher.observers),
            "Root observers do not match expected set"
        )


    def test_addObserverAgain(self):
        """
        L{DefaultLogPublisher.addObserver} with the same observer doesn't add
        it again.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = DefaultLogPublisher()
        publisher.addObserver(o1)
        publisher.addObserver(o2, filtered=True)
        publisher.addObserver(o3, filtered=False)

        # Swap filtered-ness of o2 and o3
        publisher.addObserver(o1)
        publisher.addObserver(o2, filtered=False)
        publisher.addObserver(o3, filtered=True)

        self.assertEquals(
            set((o1, o3)),
            set(publisher.filteredPublisher.observers),
            "Filtered observers do not match expected set"
        )
        self.assertEquals(
            set((o2, publisher.filters)),
            set(publisher.rootPublisher.observers),
            "Root observers do not match expected set"
        )


    def test_removeObserver(self):
        """
        L{DefaultLogPublisher.removeObserver} removes an observer.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = DefaultLogPublisher()
        publisher.addObserver(o1)
        publisher.addObserver(o2, filtered=True)
        publisher.addObserver(o3, filtered=False)
        publisher.removeObserver(o2)
        publisher.removeObserver(o3)

        self.assertEquals(
            set((o1,)),
            set(publisher.filteredPublisher.observers),
            "Filtered observers do not match expected set"
        )
        self.assertEquals(
            set((publisher.filters,)),
            set(publisher.rootPublisher.observers),
            "Root observers do not match expected set"
        )


    def test_filteredObserver(self):
        """
        L{DefaultLogPublisher.addObserver} with C{filtered=True} adds an
        observer to the C{filteredPublisher} and events are properly filtered
        when published.
        """
        namespace = __name__

        event_debug = dict(
            log_namespace=namespace,
            log_level=LogLevel.debug,
            log_format="",
        )
        event_error = dict(
            log_namespace=namespace,
            log_level=LogLevel.error,
            log_format="",
        )
        events = []

        observer = lambda e: events.append(e)

        publisher = DefaultLogPublisher()

        publisher.addObserver(observer, filtered=True)
        publisher(event_debug)
        publisher(event_error)
        self.assertNotIn(event_debug, events)
        self.assertIn(event_error, events)


    def test_filteredObserverNoFilteringKeys(self):
        """
        Event with no C{log_level} is filtered out.
        """
        event_debug = dict(log_level=LogLevel.debug)
        event_error = dict(log_level=LogLevel.error)
        event_none  = dict()
        events = []

        observer = lambda e: events.append(e)

        publisher = DefaultLogPublisher()
        publisher.addObserver(observer, filtered=True)
        publisher(event_debug)
        publisher(event_error)
        publisher(event_none)
        self.assertNotIn(event_debug, events)
        self.assertNotIn(event_error, events)
        self.assertNotIn(event_none, events)


    def test_unfilteredObserver(self):
        """
        Events are not filtered on their way to an observer added with
        C{filtered=False}.
        """
        namespace = __name__

        event_debug = dict(
            log_namespace=namespace,
            log_level=LogLevel.debug,
            log_format="",
        )
        event_error = dict(
            log_namespace=namespace,
            log_level=LogLevel.error,
            log_format="",
        )
        events = []

        observer = lambda e: events.append(e)

        publisher = DefaultLogPublisher()

        publisher.addObserver(observer, filtered=False)
        publisher(event_debug)
        publisher(event_error)
        self.assertIn(event_debug, events)
        self.assertIn(event_error, events)


    def test_trace(self):
        """
        Tracing keeps track of forwarding done by the publisher.
        """
        publisher = DefaultLogPublisher()

        event = dict(log_trace=[])

        o1 = lambda e: None

        def o2(e):
            self.assertIdentical(e, event)
            self.assertEquals(
                e["log_trace"],
                [
                    (publisher, o1),
                    (publisher, o2),
                    # Event hasn't been sent to o3 yet
                ]
            )

        def o3(e):
            self.assertIdentical(e, event)
            self.assertEquals(
                e["log_trace"],
                [
                    (publisher, o1),
                    (publisher, o2),
                    (publisher, o3),
                ]
            )

        publisher.addObserver(o1)
        publisher.addObserver(o2)
        publisher.addObserver(o3)
        publisher(event)



class FilteringLogObserverTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{FilteringLogObserver}.
    """

    def test_interface(self):
        """
        L{FilteringLogObserver} is an L{ILogObserver}.
        """
        observer = FilteringLogObserver(lambda e: None, ())
        try:
            verifyObject(ILogObserver, observer)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def filterWith(self, *filters):
        events = [
            dict(count=0),
            dict(count=1),
            dict(count=2),
            dict(count=3),
        ]

        class Filters(object):
            @staticmethod
            def twoMinus(event):
                if event["count"] <= 2:
                    return PredicateResult.yes
                return PredicateResult.maybe

            @staticmethod
            def twoPlus(event):
                if event["count"] >= 2:
                    return PredicateResult.yes
                return PredicateResult.maybe

            @staticmethod
            def notTwo(event):
                if event["count"] == 2:
                    return PredicateResult.no
                return PredicateResult.maybe

            @staticmethod
            def no(event):
                return PredicateResult.no

            @staticmethod
            def bogus(event):
                return None

        predicates = (getattr(Filters, f) for f in filters)
        eventsSeen = []
        trackingObserver = lambda e: eventsSeen.append(e)
        filteringObserver = FilteringLogObserver(trackingObserver, predicates)
        for e in events:
            filteringObserver(e)

        return [e["count"] for e in eventsSeen]


    def test_shouldLogEvent_noFilters(self):
        """
        No filters: all events come through.
        """
        self.assertEquals(self.filterWith(), [0, 1, 2, 3])


    def test_shouldLogEvent_noFilter(self):
        """
        Filter with negative predicate result.
        """
        self.assertEquals(self.filterWith("notTwo"), [0, 1, 3])


    def test_shouldLogEvent_yesFilter(self):
        """
        Filter with positive predicate result.
        """
        self.assertEquals(self.filterWith("twoPlus"), [0, 1, 2, 3])


    def test_shouldLogEvent_yesNoFilter(self):
        """
        Series of filters with positive and negative predicate results.
        """
        self.assertEquals(self.filterWith("twoPlus", "no"), [2, 3])


    def test_shouldLogEvent_yesYesNoFilter(self):
        """
        Series of filters with positive, positive and negative predicate
        results.
        """
        self.assertEquals(self.filterWith("twoPlus", "twoMinus", "no"),
                          [0, 1, 2, 3])


    def test_shouldLogEvent_badPredicateResult(self):
        """
        Filter with invalid predicate result.
        """
        self.assertRaises(TypeError, self.filterWith, "bogus")


    def test_call(self):
        """
        Test filtering results from each predicate type.
        """
        e = dict(obj=object())

        def callWithPredicateResult(result):
            seen = []
            observer = FilteringLogObserver(lambda e: seen.append(e),
                                            (lambda e: result,))
            observer(e)
            return seen

        self.assertIn(e, callWithPredicateResult(PredicateResult.yes))
        self.assertIn(e, callWithPredicateResult(PredicateResult.maybe))
        self.assertNotIn(e, callWithPredicateResult(PredicateResult.no))


    def test_trace(self):
        """
        Tracing keeps track of forwarding through the filtering observer.
        """
        event = dict(log_trace=[])

        oYes = lambda e: None
        oNo = lambda e: None

        def testObserver(e):
            self.assertIdentical(e, event)
            self.assertEquals(
                event["log_trace"],
                [
                    (publisher, yesFilter),
                    (yesFilter, oYes),
                    (publisher, noFilter),
                    # noFilter doesn't call oNo
                    (publisher, oTest),
                ]
            )
        oTest = testObserver

        yesFilter = FilteringLogObserver(oYes, (lambda e: PredicateResult.yes,))
        noFilter = FilteringLogObserver(oNo, (lambda e: PredicateResult.no,))

        publisher = LogPublisher(yesFilter, noFilter, testObserver)
        publisher(event)


class FileLogObserverTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{FileLogObserver}.
    """
    DEFAULT_TIMESTAMP = b"-"
    DEFAULT_SYSTEM = b"[-#-]"

    def buildOutput(self, timeStamp, system, text):
        """
        Build an expected output string from components.
        """
        return b" ".join((timeStamp, system, text)) + "\n"

    def buildDefaultOutput(self, text):
        """
        Build an expected output string with the default time stamp
        and system.
        """
        return self.buildOutput(
            self.DEFAULT_TIMESTAMP,
            self.DEFAULT_SYSTEM,
            text
        )

    def test_interface(self):
        """
        L{FileLogObserver} is an L{ILogObserver}.
        """
        try:
            fileHandle = StringIO()
            observer = FileLogObserver(fileHandle)
            try:
                verifyObject(ILogObserver, observer)
            except BrokenMethodImplementation as e:
                self.fail(e)
        finally:
            fileHandle.close()


    def _testObserver(self, logTime, logFormat, observerKwargs, expectedOutput):
        """
        Default time stamp format is RFC 3339
        """
        event = dict(log_time=logTime, log_format=logFormat)
        fileHandle = StringIO()
        try:
            observer = FileLogObserver(fileHandle, **observerKwargs)
            observer(event)
            output = fileHandle.getvalue()
            self.assertEquals(
                output, expectedOutput,
                "{0!r} != {1!r}".format(expectedOutput, output)
            )
        finally:
            fileHandle.close()


    def test_defaultTimeStamp(self):
        """
        Default time stamp format is RFC 3339 and offset is correct.
        """
        if tzset is None:
            raise SkipTest("Platform cannot change timezone; unable to verify offsets.")

        def setTZ(name):
            if name is None:
                del environ["TZ"]
            else:
                environ["TZ"] = name
            tzset()

        def testObserver(t_int, t_bytes):
            self._testObserver(
                t_int, u"XYZZY",
                dict(),
                self.buildOutput(t_bytes, self.DEFAULT_SYSTEM, b"XYZZY"),
            )

        def testForTimeZone(name, expectedDST, expectedSTD):
            setTZ(name)

            localDST = mktime((2006, 6, 30, 0, 0, 0, 4, 181, 1))
            localSTD = mktime((2007, 1, 31, 0, 0, 0, 2,  31, 0))

            testObserver(localDST, expectedDST)
            testObserver(localSTD, expectedSTD)

        tzIn = environ.get("TZ", None)
        try:
            # UTC
            testForTimeZone(
                "UTC",
                b"2006-06-30T00:00:00+0000",
                b"2007-01-31T00:00:00+0000",
            )

            # West of UTC
            testForTimeZone(
                "America/New_York",
                b"2006-06-30T00:00:00-0400",
                b"2007-01-31T00:00:00-0500",
            )

            # East of UTC
            testForTimeZone(
                "Europe/Berlin",
                b"2006-06-30T00:00:00+0200",
                b"2007-01-31T00:00:00+0100",
            )

            # No DST
            testForTimeZone(
                "Canada/Saskatchewan",
                b"2006-06-30T00:00:00-0600",
                b"2007-01-31T00:00:00-0600",
            )
        finally:
            setTZ(tzIn)


    def test_emptyFormat(self):
        """
        Empty format == empty log output == nothing to log.
        """
        t = mktime((2013, 9, 24, 11, 40, 47, 1, 267, 1))
        self._testObserver(
            t, u"",
            dict(),
            b"",
        )


    def test_noTimeFormat(self):
        """
        Time format is None == no time stamp.
        """
        t = mktime((2013, 9, 24, 11, 40, 47, 1, 267, 1))
        self._testObserver(
            t, u"XYZZY",
            dict(timeFormat=None),
            self.buildDefaultOutput(b"XYZZY"),
        )


    def test_alternateTimeFormat(self):
        """
        Alternate time format in output.
        """
        t = mktime((2013, 9, 24, 11, 40, 47, 1, 267, 1))
        self._testObserver(
            t, u"XYZZY",
            dict(timeFormat="%Y/%W"),
            self.buildOutput(b"2013/38", self.DEFAULT_SYSTEM, b"XYZZY")
        )


    def test_timeFormat_f(self):
        """
        "%f" supported in time format.
        """
        self._testObserver(
            1.234567, u"XYZZY",
            dict(timeFormat="%f"),
            self.buildOutput(b"234567", self.DEFAULT_SYSTEM, b"XYZZY"),
        )


    def test_noEventTime(self):
        """
        Event lacks a time == no time stamp.
        """
        self._testObserver(
            None, u"XYZZY",
            dict(),
            self.buildDefaultOutput(b"XYZZY"),
        )


    def test_multiLine(self):
        """
        Additional lines are indented.
        """
        self._testObserver(
            None, u'XYZZY\nA hollow voice says:\n"Plugh"',
            dict(),
            self.buildDefaultOutput(b'XYZZY\n\tA hollow voice says:\n\t"Plugh"'),
        )


    def test_defaultEncoding(self):
        """
        Default encoding is UTF-8.
        """
        self._testObserver(
            None, u"S\xe1nchez",
            dict(),
            self.buildDefaultOutput(b"S\xc3\xa1nchez"),
        )


    def test_alternateEncoding(self):
        """
        Alternate encoding in output.
        """
        self._testObserver(
            None, u"S\xe1nchez",
            dict(encoding="utf-16"),
            self.buildDefaultOutput(b"\xff\xfeS\x00\xe1\x00n\x00c\x00h\x00e\x00z\x00"),
        )



class PythonLogObserverTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{PythonLogObserver}.
    """

    def test_interface(self):
        """
        L{PythonLogObserver} is an L{ILogObserver}.
        """
        observer = PythonLogObserver()
        try:
            verifyObject(ILogObserver, observer)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def py_logger(self):
        """
        Create a logging object we can use to test with.
        """
        class BufferedHandler(py_logging.Handler):
            def __init__(self):
                py_logging.Handler.__init__(self)
                self.records = []

            def emit(self, record):
                self.records.append(record)

        class Container(object):
            def __init__(pl):
                pl.rootLogger = py_logging.getLogger("")

                pl.originalLevel = pl.rootLogger.getEffectiveLevel()
                pl.rootLogger.setLevel(py_logging.DEBUG)

                pl.bufferedHandler = BufferedHandler()
                pl.rootLogger.addHandler(pl.bufferedHandler)

                formatter = py_logging.Formatter(py_logging.BASIC_FORMAT)
                pl.output = StringIO()
                pl.streamHandler = py_logging.StreamHandler(pl.output)
                pl.streamHandler.setFormatter(formatter)
                pl.rootLogger.addHandler(pl.streamHandler)

            def close(pl):
                pl.rootLogger.setLevel(pl.originalLevel)
                pl.rootLogger.removeHandler(pl.bufferedHandler)
                pl.rootLogger.removeHandler(pl.streamHandler)
                pl.output.close()

        return Container()


    def logEvent(self, *events):
        """
        Send one or more events to Python's logging module, and
        capture the emitted L{logging.LogRecord}s and output stream as
        a string.

        @return: a tuple: (records, output)
        """
        pl = self.py_logger()
        try:
            observer = PythonLogObserver()
            for event in events:
                observer(event)
            return pl.bufferedHandler.records, pl.output.getvalue()
        finally:
            pl.close()


    def test_name(self):
        """
        Logger name.
        """
        records, output = self.logEvent({})

        self.assertEquals(len(records), 1)
        self.assertEquals(records[0].name, "twisted")


    def test_levels(self):
        """
        Log levels.
        """
        levelMapping = {
            None: py_logging.INFO, # default
            LogLevel.debug: py_logging.DEBUG,
            LogLevel.info: py_logging.INFO,
            LogLevel.warn: py_logging.WARNING,
            LogLevel.error: py_logging.ERROR,
            # LogLevel.critical: py_logging.CRITICAL,
        }

        # Build a set of events for each log level
        events = []
        for level, py_level in levelMapping.iteritems():
            event = {}

            # Set the log level on the event, except for default
            if level is not None:
                event["log_level"] = level

            # Remeber the Python log level we expect to see for this
            # event (as an int)
            event["py_levelno"] = int(py_level)

            events.append(event)

        records, output = self.logEvent(*events)
        self.assertEquals(len(records), len(levelMapping))

        # Check that each event has the correct level
        for i in range(len(records)):
            self.assertEquals(records[i].levelno, events[i]["py_levelno"])


    def test_callerInfo(self):
        """
        C{pathname}, C{lineno}, C{exc_info}, C{func} should
        be set properly on records.
        """
        records, output = self.logEvent({})

        self.assertEquals(len(records), 1)
        self.assertEquals(records[0].pathname, __file__)
        self.assertEquals(records[0].lineno, currentframe().f_lineno)
        self.assertEquals(records[0].exc_info, None)

        # func is missing from record, which is weird because it's
        # documented.
        #self.assertEquals(records[0].func, "test_callerInfo")

    # This isn't a regression from twisted.python.log; it just wasn't
    # tested there.
    test_callerInfo.todo = "Caller frame is always be PythonLogObserver.__call__(). Meh."


    def test_basic_format(self):
        """
        Basic formattable event passes the format along correctly.
        """
        event = dict(log_format="Hello, {who}!", who="dude")
        records, output = self.logEvent(event)

        self.assertEquals(len(records), 1)
        self.assertEquals(str(records[0].msg), "Hello, dude!")
        self.assertEquals(records[0].args, ())


    def test_basic_formatRendered(self):
        """
        Basic formattable event renders correctly.
        """
        event = dict(log_format="Hello, {who}!", who="dude")
        records, output = self.logEvent(event)

        self.assertEquals(len(records), 1)
        self.assertTrue(output.endswith(":Hello, dude!\n"))


    def test_noFormat(self):
        """
        Event with no format.
        """
        records, output = self.logEvent({})

        self.assertEquals(len(records), 1)
        self.assertEquals(
            str(records[0].msg),
            "Unable to format event {}: No log format provided"
        )



class RingBufferLogObserverTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{RingBufferLogObserver}.
    """

    def test_interface(self):
        """
        L{RingBufferLogObserver} is an L{ILogObserver}.
        """
        observer = RingBufferLogObserver(0)
        try:
            verifyObject(ILogObserver, observer)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def test_buffering(self):
        """
        Events are buffered in order.
        """
        size = 4
        events = [dict(n=n) for n in range(size/2)]
        observer = RingBufferLogObserver(size)

        for event in events:
            observer(event)
        self.assertEquals(events, list(observer))
        self.assertEquals(len(events), len(observer))


    def test_size(self):
        """
        Size of ring buffer is honored.
        """
        size = 4
        events = [dict(n=n) for n in range(size*2)]
        observer = RingBufferLogObserver(size)

        for event in events:
            observer(event)
        self.assertEquals(events[-size:], list(observer))
        self.assertEquals(size, len(observer))


    def test_clear(self):
        """
        Events are cleared by C{observer.clear()}.
        """
        size = 4
        events = [dict(n=n) for n in range(size/2)]
        observer = RingBufferLogObserver(size)

        for event in events:
            observer(event)
        self.assertEquals(len(events), len(observer))
        observer.clear()
        self.assertEquals(0, len(observer))
        self.assertEquals([], list(observer))



class LegacyLogObserverWrapperTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{LegacyLogObserverWrapper}.
    """
    
    def test_interface(self):
        """
        L{FileLogObserver} is an L{ILogObserver}.
        """
        legacyObserver = lambda e: None
        observer = LegacyLogObserverWrapper(legacyObserver)
        try:
            verifyObject(ILogObserver, observer)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def observe(self, event):
        """
        Send an event to a wrapped legacy observer.
        @return: the event as observed by the legacy wrapper
        """
        events = []

        legacyObserver = lambda e: events.append(e)
        observer = LegacyLogObserverWrapper(legacyObserver)
        observer(event)
        self.assertEquals(len(events), 1)

        return events[0]


    def forwardAndVerify(self, event):
        """
        Send an event to a wrapped legacy observer and verify that its
        data is preserved.
        @return: the event as observed by the legacy wrapper
        """
        # Send a copy: don't mutate me, bro
        observed = self.observe(dict(event))

        # Don't expect modifications
        for key, value in event.iteritems():
            self.assertIn(key, observed)
            self.assertEquals(observed[key], value)

        return observed


    def test_forward(self):
        """
        Basic forwarding.
        """
        self.forwardAndVerify(dict(foo=1, bar=2))


    def test_system(self):
        """
        Translate: C{"log_system"} -> C{"system"}
        """
        event = self.forwardAndVerify(dict(log_system="foo"))
        self.assertEquals(event["system"], "foo")


    def test_pythonLogLevel(self):
        """
        Python log level is added.
        """
        event = self.forwardAndVerify(dict(log_level=LogLevel.info))
        self.assertEquals(event["logLevel"], py_logging.INFO)


    def test_message(self):
        """
        C{"message"} key is added.
        """
        event = self.forwardAndVerify(dict())
        self.assertEquals(event["message"], ())


    def test_format(self):
        """
        Formatting is translated properly.
        """
        event = self.forwardAndVerify(dict(log_format="Hello, {who}!", who="world"))
        self.assertEquals(twistedLogging.textFromEventDict(event), "Hello, world!")


    def test_failure(self):
        """
        Failures are handled, including setting isError and why.
        """
        failure = Failure(RuntimeError("nyargh!"))
        why = "oopsie..."
        event = self.forwardAndVerify(dict(
            log_failure=failure,
            log_format=why,
        ))
        self.assertIdentical(event["failure"], failure)
        self.assertTrue(event["isError"])
        self.assertEquals(event["why"], why)



class LegacyLoggerTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{LegacyLogger}.
    """

    def test_namespace_default(self):
        """
        Default namespace is module name.
        """
        log = TestLegacyLogger(logger=None)
        self.assertEquals(log.newStyleLogger.namespace, __name__)


    def test_passThroughAttributes(self):
        """
        C{__getattribute__} on L{LegacyLogger} is passing through to Twisted's
        logging module.
        """
        log = TestLegacyLogger()

        # Not passed through
        self.assertIn("API-compatible", log.msg.__doc__)
        self.assertIn("API-compatible", log.err.__doc__)

        # Passed through
        self.assertIdentical(log.addObserver, twistedLogging.addObserver)


    def test_legacy_msg(self):
        """
        Test LegacyLogger's log.msg()
        """
        log = TestLegacyLogger()

        message = "Hi, there."
        kwargs = {"foo": "bar", "obj": object()}

        log.msg(message, **kwargs)

        self.assertIdentical(log.newStyleLogger.emitted["level"],
                             LogLevel.info)
        self.assertEquals(log.newStyleLogger.emitted["format"], message)

        for key, value in kwargs.items():
            self.assertIdentical(log.newStyleLogger.emitted["kwargs"][key],
                                 value)

        log.msg(foo="")

        self.assertIdentical(log.newStyleLogger.emitted["level"],
                             LogLevel.info)
        self.assertIdentical(log.newStyleLogger.emitted["format"], None)


    def test_legacy_err_implicit(self):
        """
        Test LegacyLogger's log.err() capturing the in-flight exception.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = {"foo": "bar", "obj": object()}

        try:
            raise exception
        except RuntimeError:
            log.err(**kwargs)

        self.legacy_err(log, kwargs, None, exception)


    def test_legacy_err_exception(self):
        """
        Test LegacyLogger's log.err() with a given exception.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = {"foo": "bar", "obj": object()}
        why = "Because I said so."

        try:
            raise exception
        except RuntimeError as e:
            log.err(e, why, **kwargs)

        self.legacy_err(log, kwargs, why, exception)


    def test_legacy_err_failure(self):
        """
        Test LegacyLogger's log.err() with a given L{Failure}.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = {"foo": "bar", "obj": object()}
        why = "Because I said so."

        try:
            raise exception
        except RuntimeError:
            log.err(Failure(), why, **kwargs)

        self.legacy_err(log, kwargs, why, exception)


    def test_legacy_err_bogus(self):
        """
        Test LegacyLogger's log.err() with a bogus argument.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = {"foo": "bar", "obj": object()}
        why = "Because I said so."
        bogus = object()

        try:
            raise exception
        except RuntimeError:
            log.err(bogus, why, **kwargs)

        errors = self.flushLoggedErrors(exception.__class__)
        self.assertEquals(len(errors), 0)

        self.assertIdentical(log.newStyleLogger.emitted["level"],
                             LogLevel.error)
        self.assertEquals(log.newStyleLogger.emitted["format"], repr(bogus))
        self.assertIdentical(log.newStyleLogger.emitted["kwargs"]["why"], why)

        for key, value in kwargs.items():
            self.assertIdentical(log.newStyleLogger.emitted["kwargs"][key],
                                 value)


    def legacy_err(self, log, kwargs, why, exception):
        errors = self.flushLoggedErrors(exception.__class__)
        self.assertEquals(len(errors), 1)

        self.assertIdentical(log.newStyleLogger.emitted["level"],
                             LogLevel.error)
        self.assertEquals(log.newStyleLogger.emitted["format"], None)
        emittedKwargs = log.newStyleLogger.emitted["kwargs"]
        self.assertIdentical(emittedKwargs["failure"].__class__, Failure)
        self.assertIdentical(emittedKwargs["failure"].value, exception)
        self.assertIdentical(emittedKwargs["why"], why)

        for key, value in kwargs.items():
            self.assertIdentical(log.newStyleLogger.emitted["kwargs"][key],
                                 value)



class Unformattable(object):
    """
    An object that raises an exception from C{__repr__}.
    """

    def __repr__(self):
        return str(1/0)
