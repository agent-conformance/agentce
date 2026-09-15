package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class ModelCallPayload extends Payload {

  private String operation;
  private ModelRef model;
  private Usage usage;
  private String inputRef;
  private String outputRef;
  private String outputMarking;
  private String error;


}