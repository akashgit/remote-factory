package com.example.app;

import com.example.core.Greeter;
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class MainTest {
    @Test
    public void testGreeter() {
        Greeter greeter = new Greeter();
        assertEquals("Hello, World!", greeter.greet("World"));
    }
}
